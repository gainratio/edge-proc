"""End-to-end EdgeProc demo: read ``catalog.json``, persist a saved index AND the model.

``docs/QUICKSTART.md`` shows the one-line ``LocalVecRuntime.from_texts`` path. This
file is its explicit cousin — the registry wiring made fully visible — and the
producer step for ``run_loop.sh``: it reads ``catalog.json``, encodes each entry
with ``TextEncoder``, builds a FAISS index, and saves it to ``--out`` (default
``./catalog_idx``). ``run_loop.sh`` then publishes that directory, syncs it onto a
consumer dir, and routes a sample task against the synced cache — all using only
the shipped CLI verbs.

**This is the build machine, and it is the only step allowed to use the network.** It
writes the embedding model to ``--model-out`` next to the index so ``publish`` chunks and
signs both, and the device that later runs ``route`` is handed a model it never has to go
fetch. Without that, a synced device still could not answer a query offline: the index
would be local but the model would not.

Because EdgeProc refuses model downloads by default, run this with the fetch explicitly
permitted::

    EDGEPROC_ALLOW_MODEL_DOWNLOAD=1 uv run python examples/quickstart.py

(or as part of ``bash examples/run_loop.sh``, which sets it for this step only).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from edgeproc_core.vector_mgmt.core.types import IndexConfig, VectorEmbedding

from edgeproc.localvec.encoder import TextEncoder
from edgeproc.localvec.faiss_index import FaissVectorIndex

_INDEX_NAME = "catalog_idx"
_MODEL_NAME = "model"


async def build_and_save_index(catalog: dict[str, str], out: Path, model_out: Path) -> None:
    """Encode ``catalog``, persist a ``FaissVectorIndex`` to ``out/`` and the model."""
    ids, texts = list(catalog), list(catalog.values())
    encoder = TextEncoder()
    index = FaissVectorIndex(_INDEX_NAME, IndexConfig(dimension=encoder.dim))
    vectors = encoder.encode_texts(texts)
    await index.insert(
        [
            VectorEmbedding(entity_id=entity_id, embedding=vector.tolist())
            for entity_id, vector in zip(ids, vectors, strict=True)
        ]
    )
    out.mkdir(parents=True, exist_ok=True)
    index.save(out)
    # Ship the model beside the index: `publish` signs whatever is under --src, so the
    # device gets its weights through the same verified bundle as its data.
    model_out.mkdir(parents=True, exist_ok=True)
    encoder.save(model_out)


def _parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parent
    parser.add_argument("--catalog", type=Path, default=here / "catalog.json")
    parser.add_argument("--out", type=Path, default=here / _INDEX_NAME)
    parser.add_argument("--model-out", type=Path, default=here / _MODEL_NAME)
    return parser.parse_args()


def main() -> None:
    args = _parse()
    catalog: dict[str, str] = json.loads(args.catalog.read_text())
    asyncio.run(build_and_save_index(catalog, args.out, args.model_out))
    print(f"saved {len(catalog)}-doc index to {args.out}")
    print(f"saved embedding model to {args.model_out} (publish this too, or the device")
    print("cannot answer a query without the network)")


if __name__ == "__main__":
    main()
