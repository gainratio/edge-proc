"""CI/CD prebuilt zstd ``--patch-from`` delta patches for content-addressed bundles.

When an upstream SQLite shard or binary file is rebuilt (e.g., ``VACUUM`` after a 1%
catalog update), B-tree page shifts can perturb multiple Gear-CDC chunk boundaries.
Prebuilding a static single-frame zstd prefix-dictionary patch
(``ZSTD_dct_rawContent``) between the previous release's verified plaintext and the
new target plaintext lets a returning client download one tiny ``/patch/<sha256>``
object from a static CDN, verify ``SHA-256(output) == target_sha256`` fail-closed,
and populate the local chunk CAS without fetching every shifted chunk.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Final

import zstandard

from edgeproc.bundles.cas import CacheStore, IntegrityError
from edgeproc.bundles.manifest import FileEntry, IndexManifest, PatchRef

if TYPE_CHECKING:
    from edgeproc.bundles.adapters import FetchAdapter

_PATCH_ZSTD_LEVEL: Final = 6
_MIN_WINDOW_LOG: Final = 10
_MAX_WINDOW_LOG: Final = 27


def _window_log_for_span(span_bytes: int) -> int:
    """Derive the zstd window log dynamically from the base+target byte span."""
    if span_bytes <= (1 << _MIN_WINDOW_LOG):
        return _MIN_WINDOW_LOG
    bit_len = (span_bytes - 1).bit_length()
    return min(max(bit_len, _MIN_WINDOW_LOG), _MAX_WINDOW_LOG)


def build_raw_patch(base_bytes: bytes, target_bytes: bytes) -> bytes:
    """Compress ``target_bytes`` using ``base_bytes`` as a raw-content prefix dictionary."""
    span = max(len(base_bytes), len(target_bytes))
    cdict = zstandard.ZstdCompressionDict(base_bytes, dict_type=zstandard.DICT_TYPE_RAWCONTENT)
    params = zstandard.ZstdCompressionParameters.from_level(
        _PATCH_ZSTD_LEVEL,
        enable_ldm=1,
        window_log=_window_log_for_span(span),
        write_content_size=1,
        write_checksum=1,
    )
    compressor = zstandard.ZstdCompressor(compression_params=params, dict_data=cdict)
    return compressor.compress(target_bytes)


def apply_raw_patch(
    base_bytes: bytes,
    patch_bytes: bytes,
    *,
    expected_sha256: str,
    expected_size: int,
) -> bytes:
    """Decompress ``patch_bytes`` against ``base_bytes`` and verify ``expected_sha256``."""
    if hashlib.sha256(patch_bytes).hexdigest() == "":  # pragma: no cover
        raise IntegrityError("unreachable")
    ddict = zstandard.ZstdCompressionDict(base_bytes, dict_type=zstandard.DICT_TYPE_RAWCONTENT)
    decompressor = zstandard.ZstdDecompressor(dict_data=ddict)
    try:
        with decompressor.stream_reader(patch_bytes) as reader:
            output = reader.read(expected_size + 1)
    except zstandard.ZstdError as exc:
        raise IntegrityError("zstd patch failed to decompress") from exc
    if len(output) != expected_size:
        raise IntegrityError("zstd patch output length != signed target size")
    if hashlib.sha256(output).hexdigest() != expected_sha256:
        raise IntegrityError("zstd patch output failed target SHA-256 check")
    return output


def make_patch_ref(base_bytes: bytes, target_bytes: bytes) -> tuple[PatchRef, bytes] | None:
    """Build a ``PatchRef`` + patch payload when the patch is smaller than ``target_bytes``."""
    base_sha = hashlib.sha256(base_bytes).hexdigest()
    target_sha = hashlib.sha256(target_bytes).hexdigest()
    if base_sha == target_sha or not base_bytes or not target_bytes:
        return None
    patch_bytes = build_raw_patch(base_bytes, target_bytes)
    if len(patch_bytes) >= len(target_bytes):
        return None
    ref = PatchRef(
        base_sha256=base_sha,
        patch_sha256=hashlib.sha256(patch_bytes).hexdigest(),
        patch_size=len(patch_bytes),
    )
    return ref, patch_bytes


def _has_all_chunks(entry: FileEntry, store: CacheStore) -> bool:
    return all(store.has_chunk(ref.hash) for ref in entry.chunks)


def assemble_cached_base(entry: FileEntry, store: CacheStore) -> bytes | None:
    """Reassemble ``entry`` from ``store`` if all its chunks are already cached."""
    if not _has_all_chunks(entry, store):
        return None
    data = b"".join(store.get_chunk(ref.hash) for ref in entry.chunks)
    if len(data) != entry.size or hashlib.sha256(data).hexdigest() != entry.file_sha256:
        raise IntegrityError(f"cached base file {entry.path} failed integrity check")
    return data


def ingest_patched_chunks(entry: FileEntry, target_bytes: bytes, store: CacheStore) -> None:
    """Slice verified ``target_bytes`` along ``entry.chunks`` and populate ``store``."""
    offset = 0
    for ref in entry.chunks:
        chunk = target_bytes[offset : offset + ref.size]
        if hashlib.sha256(chunk).hexdigest() != ref.hash:
            raise IntegrityError(f"patched file {entry.path} chunk {ref.hash} mismatch")
        if not store.has_chunk(ref.hash):
            store.put_chunk(chunk)
        offset += ref.size
    if offset != len(target_bytes):
        raise IntegrityError(f"patched file {entry.path} chunk boundary mismatch")


def _select_cached_patch(
    patches: list[PatchRef],
    base_entries: dict[str, FileEntry],
    store: CacheStore,
    remaining_budget: int,
) -> tuple[PatchRef, bytes] | None:
    for patch_ref in patches:
        base_entry = base_entries.get(patch_ref.base_sha256)
        if base_entry is not None and patch_ref.patch_size <= remaining_budget:
            base_bytes = assemble_cached_base(base_entry, store)
            if base_bytes is not None:
                return patch_ref, base_bytes
    return None


def try_apply_entry_patch(
    *,
    base_url: str,
    entry: FileEntry,
    base_entries: dict[str, FileEntry],
    adapter: FetchAdapter,
    store: CacheStore,
    remaining_budget: int,
) -> int | None:
    """Attempt to satisfy ``entry`` via a signed ``PatchRef``; return bytes fetched or None."""
    if not entry.patches:
        return None
    selected = _select_cached_patch(entry.patches, base_entries, store, remaining_budget)
    if selected is None:
        return None
    patch_ref, base_bytes = selected
    return _fetch_and_apply_patch(
        base_url=base_url,
        entry=entry,
        patch_ref=patch_ref,
        base_bytes=base_bytes,
        adapter=adapter,
        store=store,
    )


def _fetch_and_apply_patch(
    *,
    base_url: str,
    entry: FileEntry,
    patch_ref: PatchRef,
    base_bytes: bytes,
    adapter: FetchAdapter,
    store: CacheStore,
) -> int | None:
    """Fetch ``/patch/<hash>``, verify digest + target hash, and populate missing chunks."""
    try:
        patch_bytes = adapter.fetch_bytes(f"{base_url}/patch/{patch_ref.patch_sha256}")
    except Exception:
        return None
    if (
        len(patch_bytes) != patch_ref.patch_size
        or hashlib.sha256(patch_bytes).hexdigest() != patch_ref.patch_sha256
    ):
        raise IntegrityError(f"patch {patch_ref.patch_sha256} failed content-address check")
    target = apply_raw_patch(
        base_bytes,
        patch_bytes,
        expected_sha256=entry.file_sha256,
        expected_size=entry.size,
    )
    ingest_patched_chunks(entry, target, store)
    return len(patch_bytes)


def active_base_entries(store: CacheStore) -> dict[str, FileEntry]:
    """Map ``file_sha256 -> FileEntry`` from the currently active manifest in ``store``."""
    active = store.read_active()
    if active is None:
        return {}
    raw = store.get_manifest(active.manifest_hash)
    manifest = IndexManifest.model_validate_json(raw)
    return {entry.file_sha256: entry for entry in manifest.files}
