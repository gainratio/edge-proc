"""Producer: build a signed, content-addressed origin a device can ``sync_index``.

The counterpart to ``sync_index``: chunk each file (Gear-CDC), store each chunk in
a content-addressed store, record an ordered ``IndexManifest``, sign a tiny
``VersionPointer`` over the manifest's content hash, and lay the bytes out under a
flat origin dir matching the HTTP contract exactly — ``latest``, ``manifest/<hash>``,
``chunk/<hash>`` — so a ``FilesystemAdapter`` (or ``python -m http.server`` / Caddy)
serves it verbatim and a fresh device pulls it back byte-for-byte.

``store`` is the producer-side CAS: ``put_chunk`` dedupes + content-addresses every
chunk, ``put_manifest`` does the same for the manifest. The flat origin is then laid
out beside the store in the store's root so the origin dir IS what the CDN serves.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from edgeproc.bundles.manifest import (
    ChunkRef,
    FileEntry,
    IndexManifest,
    VersionPointer,
    canonical_bytes,
    manifest_digest,
    pointer_signing_bytes,
)
from edgeproc.bundles.sqlite import canonicalize_sqlite_bytes, is_sqlite_bytes

if TYPE_CHECKING:
    from edgeproc.bundles.cas import FilesystemCacheStore
    from edgeproc.bundles.chunking import GearCDC
    from edgeproc.bundles.signing import Signer

_SQLITE_SUFFIXES: tuple[str, ...] = (".sqlite", ".sqlite3", ".db")


def build_bundle(
    *,
    files: Mapping[str, bytes],
    store: FilesystemCacheStore,
    chunker: GearCDC,
    signer: Signer,
    bundle_id: str,
    version: str,
    channel: str | None = None,
    sequence: int | None = None,
    bind_identity: bool = False,
    key_id: str | None = None,
    expires_at: int | None = None,
) -> VersionPointer:
    """Chunk + store ``files``, sign the manifest pointer, lay out the flat origin.

    ``bind_identity`` (opt-in) stamps ``bundle_id`` into the SIGNED pointer so it cannot be
    cross-applied to another bundle; ``channel``/``sequence`` bind a release channel and a
    monotonic freshness counter. ``key_id`` names the signing key for a keyring consumer and
    ``expires_at`` (Unix seconds) bounds how long the pointer is accepted. All are excluded
    from the signed bytes when unset, so the default call produces the byte-identical legacy
    pointer and no consumer is affected. Stamp ``key_id``/``expires_at`` only once every
    consumer runs a version that knows them: an older consumer refuses unknown fields.
    """
    with store.mutation():
        return _build_bundle_locked(
            files=files,
            store=store,
            chunker=chunker,
            signer=signer,
            bundle_id=bundle_id,
            version=version,
            channel=channel,
            sequence=sequence,
            bind_identity=bind_identity,
            key_id=key_id,
            expires_at=expires_at,
        )


def _build_bundle_locked(
    *,
    files: Mapping[str, bytes],
    store: FilesystemCacheStore,
    chunker: GearCDC,
    signer: Signer,
    bundle_id: str,
    version: str,
    channel: str | None,
    sequence: int | None,
    bind_identity: bool,
    key_id: str | None,
    expires_at: int | None,
) -> VersionPointer:
    """Build and publish one origin snapshot while holding its mutation lock."""
    entries = [_file_entry(path, data, chunker, store) for path, data in files.items()]
    manifest = IndexManifest(bundle_id=bundle_id, version=version, files=entries)
    store.put_manifest(canonical_bytes(manifest))
    pointer = _sign_pointer(
        manifest_digest(manifest),
        version,
        signer,
        bundle_id=bundle_id if bind_identity else None,
        channel=channel,
        sequence=sequence,
        key_id=key_id,
        expires_at=expires_at,
    )
    _lay_out_origin(store, manifest, pointer)
    return pointer


def _file_entry(
    path: str, plaintext: bytes, chunker: GearCDC, store: FilesystemCacheStore
) -> FileEntry:
    """Chunk one file, store each chunk, and record its ordered chunk refs."""
    if path.endswith(_SQLITE_SUFFIXES) or is_sqlite_bytes(plaintext):
        plaintext = canonicalize_sqlite_bytes(plaintext)
    refs = [ChunkRef(hash=store.put_chunk(c), size=len(c)) for c in chunker.chunk(plaintext)]
    return FileEntry(
        path=path,
        file_type=None,
        size=len(plaintext),
        file_sha256=hashlib.sha256(plaintext).hexdigest(),
        chunks=refs,
    )


def _sign_pointer(
    digest: str,
    version: str,
    signer: Signer,
    *,
    bundle_id: str | None,
    channel: str | None,
    sequence: int | None,
    key_id: str | None,
    expires_at: int | None,
) -> VersionPointer:
    """Sign the (signature-excluded) pointer bytes; return the signed pointer.

    The preimage comes from :func:`pointer_signing_bytes`, which excludes any identity /
    freshness field left ``None`` — so an unbound pointer signs the exact legacy bytes.
    """
    unsigned = VersionPointer(
        manifest_hash=digest,
        version=version,
        bundle_id=bundle_id,
        channel=channel,
        sequence=sequence,
        key_id=key_id,
        expires_at=expires_at,
        signature="",
    )
    signature = signer.sign(pointer_signing_bytes(unsigned))
    return unsigned.model_copy(update={"signature": signature})


def _lay_out_origin(
    store: FilesystemCacheStore, manifest: IndexManifest, pointer: VersionPointer
) -> None:
    """Write the flat ``chunk/`` + ``manifest/`` + ``latest`` the CDN serves verbatim.

    For each chunk referenced by the new manifest, hardlink the store's compressed
    chunk file into ``chunk/<hash>`` if it isn't already there. A one-byte edit to a
    big file re-publishes O(changed) files, not O(all chunks). ``os.link`` falls back
    to a copy on cross-filesystem / unsupported-link errors.
    """
    root = store.root
    (root / "chunk").mkdir(parents=True, exist_ok=True)
    (root / "manifest").mkdir(parents=True, exist_ok=True)
    _require_flat_directory(root / "chunk")
    _require_flat_directory(root / "manifest")
    store.write_atomic(f"manifest/{pointer.manifest_hash}", canonical_bytes(manifest))
    wanted = {ref.hash for entry in manifest.files for ref in entry.chunks}
    _publish_chunks(store, wanted)
    _fsync_directory(root / "chunk")
    store.write_atomic("latest", pointer.model_dump_json().encode("utf-8"))


def _publish_chunks(store: FilesystemCacheStore, chunk_hashes: set[str]) -> None:
    for chunk_hash in chunk_hashes:
        _publish_chunk(store, chunk_hash)


def _publish_chunk(store: FilesystemCacheStore, chunk_hash: str) -> None:
    src = _store_chunk_path(store, chunk_hash)
    dst = store.root / "chunk" / chunk_hash
    _require_regular_leaf(dst, "published chunk")
    if not dst.exists():
        _link_or_copy(src, dst)
        return
    source_bytes = src.read_bytes()
    if dst.read_bytes() != source_bytes:
        store.write_atomic(f"chunk/{chunk_hash}", source_bytes)


def _store_chunk_path(store: FilesystemCacheStore, chunk_hash: str) -> Path:
    """The store-internal compressed chunk file (``chunks/<aa>/<hash>``)."""
    return store.root / "chunks" / chunk_hash[:2] / chunk_hash


def _link_or_copy(src: Path, dst: Path) -> None:
    """Hardlink ``src`` → ``dst``; fall back to copy on cross-fs / unsupported errors."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
        _fsync_file(dst)


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _require_flat_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ValueError("flat origin directory must be a real directory, not a symlink")


def _require_regular_leaf(path: Path, label: str) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"{label} must be a regular file, not a symlink or non-file")
