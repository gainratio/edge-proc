# Roadmap

EdgeProc is pre-1.0 (beta). This roadmap reflects what's shipped today and the near-term
direction. It's grounded in the [README](README.md) and [CHANGELOG](CHANGELOG.md) — items
marked *roadmap* are kept as Protocol seams, not yet built. Nothing here is a promise of a
date.

## Shipped (v0.5.1)

- **Deterministic router** over pluggable runtimes — no LLM in the routing path.
- **LocalVec runtime** (`[localvec]`): FAISS-backed `EMBED` / `SEARCH` / `RANK` with hybrid
  BM25 + vector RRF fusion.
- **Signed bundle / sync substrate** (`[bundles]`): content-defined chunking (GearCDC), a
  content-addressed CAS with zstd + atomic promote + GC, ed25519-signed version pointers,
  fail-closed verification of every fetch, and automatic SQLite canonicalization (`page_size=4096`,
  `journal_mode=DELETE`, `VACUUM`, zeroed header counters) inside `build_bundle`.
- **CLI** (`edgeproc`): `keygen` → `publish` → `sync` → `route`, all verifying against a
  pinned trust-root pubkey.
- **PyPI distribution** (`edge-proc`): wheels and source archives published through GitHub
  OIDC with PEP 740 provenance.
- **Trust-root keyring: `key_id`, revocation, and pointer expiry.** A consumer pins a raw
  `public.key` (a keyring of one, unchanged behavior) or a JSON keyring of 1–64 keys with a
  revocation list. A signed pointer may name its signing `key_id` (selected; unknown or
  revoked refused) and carry a signed `expires_at` (refused from that second, after the
  signature verifies). Rotation is an overlap window instead of a cutover; `edgeproc
  keyring` maintains the file; stamping is opt-in at the publisher so older consumers are
  unaffected. The `sequence` rollback floor is unchanged. Revocation is distributed by
  updating the consumer's pinned keyring — there is no remotely fetched, signed revocation
  list. Shared cross-runtime vectors pin the format with `@edgeproc/browser`. Runbook:
  [docs/OPERATIONS.md](docs/OPERATIONS.md#key-rotation-and-compromised-key-runbook).

## Near-term

These are the seams already designed into the architecture, in rough priority order:

1. **First-party WASM kernel v0** — one deterministic hot path (chunk hash/verify, BM25
   scoring, or rerank math) compiled Rust→wasm32 and shipped as a versioned `.wasm`
   artifact that executes identically in the browser and in Python via wasmtime, filling
   the existing `CUSTOM_WASM` seam (`edgeproc/core/models.py`). Definition of done: WIT
   interface + component packaging; cross-host determinism proved by the parity-fixture
   harness ("Python vs same-wasm-binary-on-both-hosts"); distributed as a signed,
   content-addressed artifact through the existing CAS/manifest pipeline. Trigger to
   start: after two consumers share the ORT-WASM standard embedder config (portfolio
   engineering standard §8.3). *(Protocol seam exists; not built.)*
2. **Biscuit capability tokens** — fine-grained, attenuable authorization on the routing /
   sync paths instead of all-or-nothing access. *(Protocol seam exists; not built.)*
3. **Sigstore keyless bundle signing** — an alternative to pinned ed25519 keys, removing the
   private-key custody burden for publishers. *(Protocol seam exists; not built.)*
4. **More runtimes behind the same router seam** — the router is runtime-agnostic; growing
   the runtime catalog beyond LocalVec is the natural next step.

## Out of scope

- A hosted service or backend. EdgeProc is **purely a library/substrate** an app embeds; the
  end-user experience belongs to the application that depends on it.

Have a use case that needs one of these sooner? Open a
[feature request](.github/ISSUE_TEMPLATE/feature_request.md).
