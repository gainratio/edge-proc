# Quickstart

Goal: clone the repo, run the gate, then walk a real catalog through the full **keygen → publish → sync → route** loop.

**Shape:** seven steps — one short Python script (step 2) and six CLI commands. The script is
there because persisting an index is library work; there is no `edgeproc build-index` verb.

**Cost**, measured on a fresh clone into a fresh venv with cold caches (Apple Silicon, fast
connection): about **70 seconds of machine time** and about **1.4 GB of disk**.

| | measured |
|---|---|
| `uv sync --all-extras` | 5.5 s, 75 packages, 947 MB venv (torch + FAISS dominate) |
| `uv run poe gate` | about 3.5 min, full test suite |
| step 2, first run | 45 s — the one-time `all-MiniLM-L6-v2` download is 87 MB of it |
| steps 3–7 | 21 s — publish and sync move that 87 MB model too |

Budget more wall-clock on a slow link. The model download happens once; later runs reuse it.

## Prereqs

- Python 3.13+
- [`uv`](https://docs.astral.sh/uv/) (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- About 1.4 GB free: a ~950 MB venv, plus ~500 MB the walkthrough writes — the 87 MB
  `all-MiniLM-L6-v2` model and its copies through `src/`, `origin/`, `cache/`, `materialized/`

## 1. Clone and gate

```bash
git clone https://github.com/hseshadr/edge-proc.git
cd edge-proc

uv sync --all-extras    # core + [localvec] + [bundles] + dev tooling
uv run poe gate         # lint + format-check + mypy strict + Radon Grade A + pytest (≥90% statement+branch cov)
```

`poe gate` is the product-quality portion of the hosted `Dagger` job. Dagger also runs the real
example, benchmark, locked dependency audit, exact snapshot plus full commit-history secret scan,
and workflow validation. A green local product gate alone is not evidence that those controls ran.

## 2. Persist a catalog index and the model that built it

A `route` call needs two things on disk: an index (generation-addressed FAISS + state files
committed by one snapshot manifest) and the embedding model, so the device can encode a query
without calling anyone.

```bash
cat > save_index.py <<'PY'
import asyncio
from pathlib import Path

from edgeproc_core.vector_mgmt.core.types import IndexConfig, VectorEmbedding

from edgeproc.localvec.encoder import TextEncoder
from edgeproc.localvec.faiss_index import FaissVectorIndex

CATALOG = {
    "p1": "red running shoes",
    "p2": "waterproof hiking boots",
    "p3": "blue denim jacket",
    "p4": "trail running sneakers",
}


async def main() -> None:
    ids, texts = list(CATALOG), list(CATALOG.values())
    encoder = TextEncoder()
    index = FaissVectorIndex("catalog_idx", IndexConfig(dimension=encoder.dim))
    await index.insert(
        [
            VectorEmbedding(entity_id=i, embedding=v.tolist())
            for i, v in zip(ids, encoder.encode_texts(texts), strict=True)
        ]
    )
    index.save(Path("catalog_idx"))
    encoder.save(Path("model"))


asyncio.run(main())
PY

EDGEPROC_ALLOW_MODEL_DOWNLOAD=1 uv run python save_index.py
#   catalog_idx/snapshots/{manifest,FAISS,state} and an 87 MB model/
```

`EDGEPROC_ALLOW_MODEL_DOWNLOAD=1` is the only network access in this walkthrough. You are on a
build machine, so fetching the model here is fine; the device is never allowed to, which is
what makes step 5 genuinely offline. Without the opt-in this step refuses rather than
downloading — a fetch is something you ask for, never a fallback.

## 3. Sign a release on the publisher

`keygen` mints an ed25519 keypair. **`public.key` is the pin** a consumer trusts; distribute it out-of-band. **`private.key` never leaves the publisher.**

```bash
uv run edgeproc keygen --out keys
#   wrote keys/private.key and keys/public.key
#   key_id <16 lowercase hex chars — the key's name in a keyring>

mkdir -p src && cp -r catalog_idx model src/

uv run edgeproc publish \
    --src src \
    --origin-dir origin \
    --key keys/private.key \
    --bundle-id catalog \
    --version 1.0.0 \
    --pretty
#   published v1.0.0 manifest=4587411eea91
```

`--src` holds the index **and** the model, so both are chunked and signed under the same key. The model is payload, not something the device fetches separately from a model registry.

`origin/` now holds the full CDN contract: `latest` (the signed pointer), `manifest/<hash>`, and `chunk/<hash>` (one zstd blob per unique chunk). Point a static server or CDN at it as-is.

## 4. Sync onto a consumer

Pull the signed bundle into a fresh cache, trusting **only** the pinned pubkey. `--materialize-to` reassembles the synced files into a plain directory so a follow-on `route` can read them directly.

```bash
uv run edgeproc sync \
    --base-url origin \
    --cache-dir cache \
    --key keys/public.key \
    --materialize-to materialized \
    --pretty
#   synced v1.0.0 manifest=4587411eea91 chunks_fetched=2152 chunks_reused=0 bytes_fetched=83404167
```

83 MB because this is the first sync and the model is nearly all of it; step 6 shows what a later one costs. `materialized/` now holds `catalog_idx/` and `model/`, both verified against the pinned key.

Without `--key` (and without `EDGEPROC_TRUST_ROOT_PUBKEY_PATH` set) `sync` refuses to run — an unverifiable pull is rejected fail-closed.

## 5. Route a task against the delivered index

`--model-path` points at the model that just arrived in the bundle. This step needs no network, and if you leave the flag off it refuses rather than fetching one.

```bash
cat > task.json <<'JSON'
{"kind": "search", "payload": {"query": "shoes for running", "k": 3}, "privacy_mode": "local_only"}
JSON

uv run edgeproc route \
    --index-dir materialized/catalog_idx \
    --model-path materialized/model \
    --task task.json \
    --pretty
#   success=True runtime=localvec latency=109.6ms
#     p1  0.219
#     p4  0.246
#     p2  0.556
```

Distances are deterministic for the same model and catalog; `latency` varies by machine.

Drop `--model-path` (with no `EDGEPROC_MODEL_PATH` set) and you get the refusal instead:

```bash
uv run edgeproc route --index-dir materialized/catalog_idx --task task.json --pretty
#   [config.missing] no local embedding model is configured, so EdgeProc will not load
#   'sentence-transformers/all-MiniLM-L6-v2': fetching it would need the network. ...
```

The exit code mirrors `success` (`0` ok, `1` for `no_runtime_accepted` or any verification failure), so scripts can branch on it without parsing JSON.

## 6. Test a delta release

Publish a `1.0.1` with a tiny signed release note. This leaves the valid generation under
`src/catalog_idx/snapshots/` untouched. Re-`sync` fetches only the new chunk
(`chunks_reused > chunks_fetched`):

```bash
printf 'tiny edit\n' > src/release-note.txt

uv run edgeproc publish \
    --src src --origin-dir origin --key keys/private.key \
    --bundle-id catalog --version 1.0.1 --pretty
#   published v1.0.1 manifest=3f0941c9725a

uv run edgeproc sync \
    --base-url origin --cache-dir cache --key keys/public.key \
    --materialize-to materialized --pretty
#   synced v1.0.1 manifest=3f0941c9725a chunks_fetched=1 chunks_reused=2152 bytes_fetched=19
```

19 compressed bytes instead of the original 83 MB — one new chunk fetched, the other 2,152
reused, the whole model among them.

## 7. Try a tampered origin

Corrupt any chunk under `origin/chunk/` and sync into a fresh cache:

```bash
printf 'corrupted' > "origin/chunk/$(ls origin/chunk | head -1)"

uv run edgeproc sync \
    --base-url origin --cache-dir cache3 --key keys/public.key --pretty
#   [bundle.integrity_failed] sync failed: stored chunk failed to decompress
echo $?
#   1

ls cache3
#   chunks		manifests
```

A healthy cache has an `active` pointer file; `cache3/` has none, because the bad version was
never promoted. That's the fail-closed contract in one command.

Sync also refuses to run at all without a pinned key:

```bash
uv run edgeproc sync --base-url origin --cache-dir cache2 --pretty
#   [config.missing] no trust root: pass --key or set EDGEPROC_TRUST_ROOT_PUBKEY_PATH (refusing to sync)
```

## Next steps

- Read [ARCHITECTURE.md](ARCHITECTURE.md) for the module map and the security model.
- See [`examples/`](../examples/) for a registry-wired in-process version that doesn't use the CLI.
- Prefer pictures? [ARCHITECTURE.md](ARCHITECTURE.md) draws the same loop as three diagrams.

## Use it from Python

The same search, in-process, without the CLI. `model_path` is the library-level equivalent of
`--model-path`; without it `TextEncoder()` raises rather than reaching for the hub.

```python
import asyncio
from pathlib import Path

from edgeproc import EdgeProc, PrivacyMode, RuntimeRegistry, Task, TaskKind
from edgeproc.localvec.encoder import TextEncoder
from edgeproc.localvec.runtime import LocalVecRuntime

CATALOG = {"p1": "red running shoes", "p2": "waterproof hiking boots", "p3": "trail sneakers"}


async def main() -> None:
    encoder = TextEncoder(model_path=Path("materialized/model"))
    runtime = await LocalVecRuntime.from_texts(CATALOG, encoder=encoder)
    registry = RuntimeRegistry(); registry.register(runtime)
    result = await EdgeProc(registry=registry).run(
        Task(kind=TaskKind.SEARCH, payload={"query": "shoes for running"}, privacy_mode=PrivacyMode.LOCAL_ONLY)
    )
    for entity_id, distance in result.payload["results"]:
        print(f"  {entity_id}  {CATALOG[entity_id]:<24} distance={distance:.3f}")


asyncio.run(main())
```

```text
  p1  red running shoes        distance=0.219
  p3  trail sneakers           distance=0.374
  p2  waterproof hiking boots  distance=0.556
```

Swap `TaskKind.SEARCH` for `TaskKind.EMBED` (raw vectors) or `TaskKind.RANK` (keyword +
meaning-based ranking combined).

This needs a model directory at `materialized/model`, which step 4 writes.

## Going over the wire

Add `--http` to `sync` and serve `origin/` over any static HTTP server / CDN — that's the production deployment shape. The contract is identical; only the transport changes.
