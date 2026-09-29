# Configuration

EdgeProc reads its settings from environment variables (or a `.env` file) through
`EdgeProcSettings` in [`edgeproc/core/settings.py`](../edgeproc/core/settings.py). This page lists
every setting. A test (`tests/test_release_contract_docs.py`) checks this table against the
settings object field for field, so a setting cannot be added without being documented here.

Settings are read lazily, when first needed rather than at import time. EdgeProc validates
the settings below but
ignores unrelated host variables, so an embedded library coexists with the application's own
environment. Env vars use the `EDGEPROC_` prefix (except the HF token, which uses the
ecosystem-standard `HF_TOKEN`). Secrets (the private signing key, `HF_TOKEN`) belong in files
or the environment, never in the repository — `*.key` is git-ignored and CI scans for leaked
secrets.

| Setting | Env var | Default | Purpose |
| --- | --- | --- | --- |
| `model_name` | `EDGEPROC_MODEL_NAME` | `sentence-transformers/all-MiniLM-L6-v2` | Embedding model, as a hub reference. Only used when a download is permitted. |
| `model_path` | `EDGEPROC_MODEL_PATH` | `None` | Local model directory — the offline path. Point it at what `sync --materialize-to` wrote. |
| `model_digest` | `EDGEPROC_MODEL_DIGEST` | `None` | sha256 pin for `model_path`; a mismatch is refused. Unset ⇒ no check. |
| `allow_model_download` | `EDGEPROC_ALLOW_MODEL_DOWNLOAD` | `False` | Permit fetching the model from the hub. Off by default: no local model ⇒ refused, never fetched. |
| `hf_token` | `HF_TOKEN` | `None` | Hugging Face auth token. |
| `default_k` | `EDGEPROC_DEFAULT_K` | `10` | Default top-k results. |
| `http_timeout` | `EDGEPROC_HTTP_TIMEOUT` | `30.0` | Bundle HTTP fetch timeout (s). |
| `mutation_lock_timeout` | `EDGEPROC_MUTATION_LOCK_TIMEOUT` | `30.0` | Bounded cross-process publish/sync/promote/GC lock wait (s). |
| `snapshot_lock_timeout` | `EDGEPROC_SNAPSHOT_LOCK_TIMEOUT` | `30.0` | Bounded cross-process FAISS save/load/migration/GC lock wait (s). |
| `task_budget_ms` | `EDGEPROC_TASK_BUDGET_MS` | `5000` | Default per-task latency budget. |
| `task_budget_memory_mb` | `EDGEPROC_TASK_BUDGET_MEMORY_MB` | `256` | Default per-task memory budget. |
| `max_in_flight_memory_mb` | `EDGEPROC_MAX_IN_FLIGHT_MEMORY_MB` | `512` | Sum of declared task reservations admitted concurrently by one `EdgeProc` instance. |
| `max_materialize_bytes` | `EDGEPROC_MAX_MATERIALIZE_BYTES` | `256 MiB` | Maximum one file materialized into a returned `bytes` value. |
| `max_decompressed_bytes` | `EDGEPROC_MAX_DECOMPRESSED_BYTES` | `64 MiB` | Ceiling on one chunk's decompressed plaintext — refuses a zstd bomb before it exhausts memory. A legitimate chunk is ≤256 KiB, so this never rejects real data. |
| `max_fetch_bytes` | `EDGEPROC_MAX_FETCH_BYTES` | `256 MiB` | Ceiling on a single HTTP fetch body (pointer, manifest, or chunk) — bounds a hostile origin. |
| `max_sync_total_bytes` | `EDGEPROC_MAX_SYNC_TOTAL_BYTES` | `4 GiB` | Aggregate bytes one `sync` will pull before refusing — disk-exhaustion defense against a runaway manifest. |
| `max_sync_files` | `EDGEPROC_MAX_SYNC_FILES` | `100000` | Aggregate file count one `sync` will pull before refusing, for the same reason. |
| `rrf_k_window` | `EDGEPROC_RRF_K_WINDOW` | `60` | RRF rank-window constant for hybrid fusion. |
| `trust_root_pubkey_path` | `EDGEPROC_TRUST_ROOT_PUBKEY_PATH` | `None` | Pinned sync trust root: a raw `public.key` or a JSON keyring (none ⇒ `sync` refused). |
| `publish_stamp_key_id` | `EDGEPROC_PUBLISH_STAMP_KEY_ID` | `False` | Sign the signing key's `key_id` into published pointers. Upgrade every consumer first. |
| `publish_expires_in` | `EDGEPROC_PUBLISH_EXPIRES_IN` | `None` | Sign `expires_at = now + duration` (seconds, or `90s`/`30m`/`12h`/`7d`/`2w`) into published pointers. Upgrade every consumer first. |

That is the complete set — all 21 fields of `EdgeProcSettings`. A test asserts this table
matches the settings object field-for-field, so a new setting cannot ship undocumented.

One more environment variable exists that is deliberately **not** an `EdgeProcSettings`
field, because it steers CLI output rather than library behavior:

| Env var | Default | Purpose |
| --- | --- | --- |
| `EDGEPROC_ERROR_FORMAT` | `text` | Set to `json` and every fail-closed exit prints one [RFC 9457 Problem Details](https://www.rfc-editor.org/rfc/rfc9457) object on stderr instead of a text line — so a CI step or supervising process branches on `type` rather than pattern-matching prose. |

```console
$ EDGEPROC_ERROR_FORMAT=json edgeproc sync --base-url ./origin --cache-dir ./cache --key absent.key
{"detail": "could not read trust-root key absent.key: [Errno 2] No such file or directory: 'absent.key'", "field": "--key", "title": "A required setting is missing: --key.", "type": "config.missing"}
```

It is read straight from the environment rather than through `EdgeProcSettings` on purpose:
this runs on the failure path, and building the settings object there would let an unrelated
malformed variable raise *while reporting another error*, swallowing the refusal being reported.
