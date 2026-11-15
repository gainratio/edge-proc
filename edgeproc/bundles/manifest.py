"""Bundle manifest models — v2 chunked, content-addressed, signed by version pointer.

A bundle is a versioned set of content-addressed files split into deduped chunks.
The :class:`VersionPointer` is the *only* signed object; it names the
:class:`IndexManifest` by its content hash, and the manifest names each chunk by
content hash. Tampering with any layer fails its hash or signature check.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any, Final

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
)

from edgeproc.bundles.containment import ensure_safe_relpath

# Mirrors shared-libs' convention: opaque metadata values are scalars, never `Any`.
Scalar = str | int | float | bool | None
_SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")


def validate_sha256_hex(value: str) -> str:
    """Return a canonical bare SHA-256 digest or reject it at the trust boundary."""
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid SHA-256 digest: expected 64 lowercase hexadecimal characters")
    return value


type Sha256Hex = Annotated[str, AfterValidator(validate_sha256_hex)]

_KEY_ID_PATTERN: Final = re.compile(r"[0-9a-f]{16}")
#: Largest integer every JSON runtime represents exactly (``Number.MAX_SAFE_INTEGER`` + 1).
#: A signed integer field must round-trip identically through a browser consumer.
JSON_SAFE_INTEGER_LIMIT: Final = 2**53


def validate_key_id(value: str) -> str:
    """Return a canonical ``key_id`` (16 lowercase hex) or reject it at the trust boundary."""
    if _KEY_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid key_id: expected 16 lowercase hexadecimal characters")
    return value


type KeyId = Annotated[str, AfterValidator(validate_key_id)]


def _integral_number(value: object) -> object:
    """Read an integral JSON number (``1767225600.0``) as the integer it is.

    ``JSON.parse`` cannot tell ``1767225600.0`` from ``1767225600``, so the browser runtime
    accepts both; Python must accept the same pointers. A fractional, infinite, or NaN
    float passes through unchanged and the strict integer check refuses it.
    """
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


#: Unix seconds: an integer 0 < v < 2**53. ``strict`` refuses a bool, a numeric string, and
#: a fractional number, and the signing preimage always spells the value as an integer.
type UnixSeconds = Annotated[
    int,
    BeforeValidator(_integral_number),
    Field(strict=True, gt=0, lt=JSON_SAFE_INTEGER_LIMIT),
]


class ChunkRef(BaseModel):
    """One content-defined chunk: ``hash`` is the bare hex sha256 of its plaintext."""

    model_config = ConfigDict(extra="forbid")

    hash: Sha256Hex
    size: int  # uncompressed chunk length in bytes


class PatchRef(BaseModel):
    """Prebuilt zstd ``--patch-from`` delta from ``base_sha256`` to this ``FileEntry``."""

    model_config = ConfigDict(extra="forbid")

    base_sha256: Sha256Hex
    patch_sha256: Sha256Hex
    patch_size: int = Field(gt=0)


class FileEntry(BaseModel):
    """A file as an ordered list of chunks (order = reassembly order)."""

    model_config = ConfigDict(extra="forbid")

    path: str
    file_type: str | None = None
    size: int  # total uncompressed file length
    file_sha256: Sha256Hex  # bare hex sha256 of the whole reassembled file
    chunks: list[ChunkRef]
    patches: list[PatchRef] | None = None

    @model_serializer(mode="wrap")
    def _omit_unset_patches(self, handler: SerializerFunctionWrapHandler) -> Any:
        """Omit ``patches`` when unset so legacy manifest canonical bytes never change."""
        data = handler(self)
        if "patches" in data and not data["patches"]:
            del data["patches"]
        return data

    @field_validator("path")
    @classmethod
    def _reject_unsafe_path(cls, value: str) -> str:
        """Refuse traversal/absolute paths at the model boundary (fail-closed).

        The path is written to disk on materialize; a compromised or malformed
        origin must not be able to smuggle ``../`` or ``/abs`` past parsing.
        """
        return ensure_safe_relpath(value)


class IndexManifest(BaseModel):
    """v2 chunked manifest; authenticated by its content hash, not an embedded sig."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 2
    bundle_id: str
    version: str
    files: list[FileEntry]
    metadata: dict[str, Scalar] = Field(default_factory=dict)


class VersionPointer(BaseModel):
    """Signed pointer to a manifest; ``signature`` is detached over the rest.

    ``bundle_id``/``channel`` optionally BIND the signature to a bundle identity and
    release channel; ``sequence`` is an optional monotonic freshness counter. ``key_id``
    names the trust-root key that signed the pointer (so a keyring consumer selects it,
    refuses it when revoked, and fails closed when it is unknown), and ``expires_at`` is
    the Unix second from which a consumer refuses the pointer. All five default ``None``
    and are excluded from the signed preimage when unset (see
    :func:`pointer_signing_bytes`), so an already-signed legacy pointer — which carries
    none of them — verifies byte-for-byte and existing verification is unchanged.

    ``key_id``/``expires_at`` are also left OUT of the serialized pointer when unset (see
    :meth:`_omit_unset_keyring_fields`): an older consumer's model forbids unknown keys, so
    a publisher that does not stamp them writes the exact pre-keyring ``latest`` bytes.
    """

    model_config = ConfigDict(extra="forbid")

    manifest_hash: Sha256Hex  # hex sha256 of the manifest's canonical bytes
    version: str
    bundle_id: str | None = None  # identity binding (optional; None ⇒ legacy preimage)
    channel: str | None = None  # release-channel binding (optional)
    sequence: int | None = Field(default=None, ge=0)  # monotonic freshness counter (optional)
    key_id: KeyId | None = None  # signing key's id in the consumer's keyring (optional)
    expires_at: UnixSeconds | None = None  # refused at/after this Unix second (optional)
    signature: str  # ed25519 over pointer_signing_bytes(self)

    @model_serializer(mode="wrap")
    def _omit_unset_keyring_fields(self, handler: SerializerFunctionWrapHandler) -> Any:
        """Drop an unset ``key_id``/``expires_at`` so the legacy wire bytes never change.

        The older fields keep serializing ``null`` exactly as they always have; only the
        keyring fields — which an older consumer would reject even as ``null`` — vanish.
        """
        data = handler(self)
        for name in _KEYRING_FIELDS:
            if name in data and data[name] is None:
                del data[name]
        return data


#: Fields introduced with the trust-root keyring. Unset, they are absent from BOTH the
#: signing preimage and the serialized pointer.
_KEYRING_FIELDS: Final = ("key_id", "expires_at")


# Identity/freshness fields added after v0. They are excluded from the signing preimage
# whenever they are unset, so a pointer carrying none of them hashes IDENTICALLY to the
# legacy {manifest_hash, version} bytes — every already-signed pointer still verifies.
_POINTER_OPTIONAL_FIELDS: Final = ("bundle_id", "channel", "sequence", *_KEYRING_FIELDS)


def pointer_signing_bytes(pointer: VersionPointer) -> bytes:
    """The exact bytes signed/verified for ``pointer`` (backward-compatible).

    Excludes ``signature`` plus any identity/freshness field left ``None``. A pointer that
    binds no identity therefore produces the byte-identical legacy preimage; a field that
    IS set is folded in, binding the signature to that bundle / channel / sequence.
    """
    exclude = {"signature"}
    exclude.update(f for f in _POINTER_OPTIONAL_FIELDS if getattr(pointer, f) is None)
    return canonical_bytes(pointer, exclude=exclude)


def is_expired(pointer: VersionPointer, now: float) -> bool:
    """True when ``pointer`` carries an ``expires_at`` and ``now`` has reached it.

    Inclusive: at ``now == expires_at`` the pointer is already expired. A pointer with no
    ``expires_at`` never expires (the pre-keyring behavior). Only meaningful AFTER the
    signature verified — an unsigned expiry must never decide anything.
    """
    return pointer.expires_at is not None and now >= pointer.expires_at


def is_fresh_sequence(incoming: VersionPointer, active: VersionPointer) -> bool:
    """Freshness predicate for a downstream anti-replay guard (monotonic ``sequence``).

    True only when ``incoming.sequence`` is STRICTLY greater than ``active.sequence`` — an
    equal or lower sequence is a stale replay (non-fresh).

    FAIL-CLOSED on a missing counter: once ``active`` carries a sequence, an ``incoming``
    that carries none proves nothing and is NOT fresh. Answering an active counter with
    silence used to pass, so deleting the field from a validly signed older pointer
    defeated the guard outright.

    An ``active`` with no sequence is the one undecidable case — there is no counter state
    to roll back to — so this returns True and the caller's version-based anti-rollback
    guard decides. That is what keeps a pre-sequence store upgradable.
    """
    if active.sequence is None:
        return True
    if incoming.sequence is None:
        return False
    return incoming.sequence > active.sequence


def canonical_bytes(model: BaseModel, *, exclude: set[str] | None = None) -> bytes:
    """Deterministic, reproducible byte encoding — the exact bytes hashed/signed.

    ``sort_keys`` sorts dict keys recursively but preserves list order, so a
    ``FileEntry.chunks`` sequence keeps its reassembly order.

    CRITICAL: this serialization format is frozen by ``VersionPointer.manifest_hash``.
    Any change (key order, separators, encoding) re-hashes every manifest and breaks
    verification against all existing bundles — it must never change without a migration.
    """
    payload = model.model_dump(mode="json", exclude=exclude)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def manifest_digest(manifest: IndexManifest) -> str:
    """Bare hex sha256 of the manifest's canonical bytes (a ``VersionPointer`` target)."""
    return hashlib.sha256(canonical_bytes(manifest)).hexdigest()
