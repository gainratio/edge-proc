"""LocalVecRuntime — a Runtime backed by a vector index, an encoder, and BM25.

Serves three TaskKinds locally:

- ``EMBED``  → encode ``payload.texts`` into vectors.
- ``SEARCH`` → vector similarity over the index for ``payload.query``.
- ``RANK``   → hybrid keyword + vector, fused with Reciprocal Rank Fusion.

It only accepts ``PrivacyMode.LOCAL_ONLY``: a cloud privacy mode is rejected, never
silently downgraded. Bad input becomes a failure envelope — nothing raises across
the runtime boundary.
"""

from __future__ import annotations

from time import perf_counter
from typing import Final, cast

from edgeproc_core.vector_mgmt.core.types import IndexConfig, VectorEmbedding, VectorIndex

from edgeproc._version import __version__
from edgeproc.core.models import (
    DEFAULT_SIGNATURE_STATUS,
    CapabilityVerdict,
    JsonValue,
    PrivacyMode,
    Provenance,
    ResultEnvelope,
    Task,
    TaskKind,
)
from edgeproc.core.settings import EdgeProcSettings
from edgeproc.localvec.encoder import Encoder
from edgeproc.localvec.fusion import reciprocal_rank_fusion
from edgeproc.localvec.searcher import KeywordSearcher

_SUPPORTED: Final[frozenset[TaskKind]] = frozenset({TaskKind.EMBED, TaskKind.SEARCH, TaskKind.RANK})


class LocalVecRuntime:
    """Local embed/search/rank over a shared-libs ``VectorIndex``."""

    name = "localvec"

    def __init__(
        self,
        encoder: Encoder,
        index: VectorIndex,
        keyword: KeywordSearcher | None = None,
    ) -> None:
        self._encoder = encoder
        self._index = index
        self._keyword = keyword
        self._default_k = EdgeProcSettings().default_k

    @classmethod
    async def from_texts(
        cls,
        catalog: dict[str, str],
        *,
        encoder: Encoder,
        index_name: str = "catalog",
    ) -> LocalVecRuntime:
        """Encode ``catalog`` (``{id: text}``), build a FAISS index + BM25, return a runtime.

        The one-call wiring path ``docs/QUICKSTART.md`` uses. Use the explicit
        constructor when you already have an index (e.g. loaded from disk) or want a
        different ``VectorIndex`` implementation.
        """
        # Import locally to keep the module dep-light: FaissVectorIndex pulls FAISS.
        from edgeproc.localvec.faiss_index import FaissVectorIndex  # noqa: PLC0415

        ids, texts = list(catalog), list(catalog.values())
        index = FaissVectorIndex(index_name, IndexConfig(dimension=encoder.dim))
        await index.insert(
            [
                VectorEmbedding(entity_id=entity_id, embedding=vector.tolist())
                for entity_id, vector in zip(ids, encoder.encode_texts(texts), strict=True)
            ]
        )
        return cls(encoder, index, KeywordSearcher.from_texts(texts, ids))

    def can_handle(self, task: Task) -> CapabilityVerdict:
        if task.privacy_mode != PrivacyMode.LOCAL_ONLY:
            return CapabilityVerdict.REJECT_CAPABILITY
        if task.kind in _SUPPORTED:
            return CapabilityVerdict.ACCEPT
        return CapabilityVerdict.REJECT_KIND

    async def execute(self, task: Task) -> ResultEnvelope:
        start = perf_counter()
        try:
            payload = await self._dispatch(task)
        except (ValueError, KeyError, TypeError) as exc:
            return self._envelope(task, success=False, payload={}, start=start, error=str(exc))
        return self._envelope(task, success=True, payload=payload, start=start)

    async def _dispatch(self, task: Task) -> dict[str, JsonValue]:
        if task.kind == TaskKind.EMBED:
            return self._embed(task)
        if task.kind == TaskKind.SEARCH:
            return await self._search(task)
        return await self._rank(task)

    def _embed(self, task: Task) -> dict[str, JsonValue]:
        vectors = self._encoder.encode_texts(_require_texts(task))
        rows: list[JsonValue] = [row.tolist() for row in vectors]
        return {"embeddings": rows}

    async def _search(self, task: Task) -> dict[str, JsonValue]:
        query = self._encoder.encode_query(_require_query(task)).tolist()
        hits = await self._index.search(query, _require_k(task, self._default_k))
        return {"results": _as_rows(hits)}

    async def _rank(self, task: Task) -> dict[str, JsonValue]:
        if self._keyword is None:
            raise ValueError("RANK requires a keyword searcher")
        text = _require_query(task)
        k = _require_k(task, self._default_k)
        keyword_hits = self._keyword.search(text, k=k)
        vector_hits = await self._index.search(self._encoder.encode_query(text).tolist(), k)
        return {"results": _as_rows(reciprocal_rank_fusion(keyword_hits, vector_hits)[:k])}

    def _envelope(
        self,
        task: Task,
        *,
        success: bool,
        payload: dict[str, JsonValue],
        start: float,
        error: str | None = None,
    ) -> ResultEnvelope:
        return ResultEnvelope(
            request_id=task.request_id,
            task_kind=task.kind,
            success=success,
            payload=payload,
            runtime_used=self.name,
            privacy_mode=task.privacy_mode,
            confidence=1.0 if success else 0.0,
            latency_ms=(perf_counter() - start) * 1000.0,
            provenance=Provenance(
                signature_status=DEFAULT_SIGNATURE_STATUS, runtime_version=__version__
            ),
            error=error,
        )


def _as_rows(hits: list[tuple[str, float]]) -> list[JsonValue]:
    return [cast(JsonValue, [doc, score]) for doc, score in hits]


def _require_texts(task: Task) -> list[str]:
    texts = task.payload.get("texts")
    if not isinstance(texts, list) or not all(isinstance(item, str) for item in texts):
        raise ValueError("payload.texts must be a list of strings")
    return cast("list[str]", texts)


def _require_query(task: Task) -> str:
    query = task.payload.get("query")
    if not isinstance(query, str):
        raise ValueError("payload.query must be a string")
    return query


def _require_k(task: Task, default: int) -> int:
    k = task.payload.get("k", default)
    if not isinstance(k, int):
        raise ValueError("payload.k must be an int")
    return k
