# EdgeProc operating contract

TL;DR: EdgeProc keeps task execution local by default and treats bundle delivery as
untrusted input. Production consumers must pin a public key plus bundle identity/channel,
use monotonic sequences, retain the default resource ceilings, and own the SLA of the host
application. The library supplies fail-closed integrity, bounded fetches, crash-atomic CAS
promotion, and a repeatable performance gate; it is not a hosted service.

## Threat model and trust boundaries

The protected assets are the signing key, pinned verification key, active bundle pointer,
content-addressed cache, local task payloads, index contents, and result envelopes. The
main adversaries are a malicious or compromised CDN/origin, an on-path attacker, a stale
but valid signed release, malformed manifests/chunks, decompression bombs, path traversal,
and concurrent local writers.

The private signing key is trusted and stays only on the publisher. Consumers receive the
trust root — one public key, or a keyring of several with a revocation list — out of band. The CDN and all downloaded bytes are untrusted. A production
consumer should publish with `bundle_id`, `channel`, and `sequence`, then sync with
`expected_bundle_id` and `expected_channel`; legacy unbound pointers remain compatible but
do not provide cross-bundle identity protection.

Promotion is fail-closed on freshness, not merely on the absence of disproof. Replacing the
active pointer requires PROOF that the incoming one is fresher: a strictly-greater monotonic
`sequence`, or a strictly-greater PEP 440 `version`. Either comparison proving staleness
refuses the promote, and so does neither comparison being able to speak. Two cases where
nothing can speak: a version PEP 440 cannot parse, and a version EQUAL to the active one —
two different bundles can wear one label, so an equal version says nothing about which is
newer. A signature proves who published a pointer and never how recent it is, so a replayed
pointer is validly signed by construction. Publishers who re-ship under one label, or whose
version scheme PEP 440 cannot parse (date strings, build labels), must bind `--sequence` to
keep shipping. Re-promoting the byte-identical active pointer is a no-op and needs no proof.

The guard's own input is held to the same bar. `active` missing entirely means nothing has
been promoted and the first promote needs no proof; an `active` that exists but cannot be
read as a pointer is refused as `[bundle.integrity_failed]`, never treated as "nothing is
active" — that answer would skip the freshness check altogether.

Before promotion, EdgeProc verifies the pointer signature (under the key the pointer names,
never a revoked one), any signed expiry, pinned identity, manifest hash,
manifest identity, every chunk hash, and complete file reassembly. Paths are contained
under the selected root. HTTP bodies, decompressed chunks, aggregate sync bytes,
materialized file bytes, file counts, and lock waits have hard ceilings. A shared filesystem mutation lock serializes
publish, sync, promote, garbage collection, and CLI materialization across cooperating
threads and processes, so a stale last writer cannot bypass the rollback check.

Out of scope: preventing compromise of the publisher's private key (containing one is the
revocation runbook below), a local attacker who can already
write the consumer's cache or process memory, vulnerabilities inside a consumer-supplied
runtime/telemetry sink, and availability of a consumer-selected CDN or model registry.

## Privacy and data flow

| Data | Default destination | Network behavior | Retention/owner |
| --- | --- | --- | --- |
| `Task.payload` and query text | Selected in-process runtime | `LocalVecRuntime` accepts only `local_only` and sends no query request | Host process; consumer owns deletion |
| `ResultEnvelope` | Default `NullSink` | No telemetry egress by default; a custom sink is consumer code and may egress | Consumer-defined |
| Signed bundle bytes | Consumer-selected filesystem or HTTP origin | `GET /latest`, `/manifest/<hash>`, and `/chunk/<hash>`; the origin sees ordinary HTTP metadata, never task payloads | Local CAS until consumer deletion/GC |
| Embedding model | Local directory named by `EDGEPROC_MODEL_PATH` — normally what `sync --materialize-to` wrote | No egress by default. With no local model configured `TextEncoder` refuses (`config.missing`) before any loader is built; it does not fall back to a download. A fetch happens only when `EDGEPROC_ALLOW_MODEL_DOWNLOAD` is set — a build-machine setting — and `HF_TOKEN` is used only on that path. `EDGEPROC_MODEL_DIGEST` pins a model that did not arrive in the signed bundle; a mismatch is refused as `bundle.integrity_failed` | Consumer-owned model directory; a Hugging Face cache exists only if a download was permitted |
| Signing key | Publisher filesystem | Never transmitted by EdgeProc | Publisher deletes/rotates it |

The library has no account database, analytics endpoint, or hidden telemetry. Deleting a
local index/cache and any consumer-owned sink records removes EdgeProc's retained copies;
there is no EdgeProc server-side user record to erase. Filesystem remanence, backups,
custom runtimes, custom sinks, model caches, and CDN logs remain the host operator's
responsibility.

## Reliability and recovery contract

- **Crash-atomic activation:** the active pointer is a same-filesystem, fsynced atomic
  replace. Publisher `latest` and manifest artifacts use the same primitive. A reader
  observes the old pointer or the new pointer, never a torn pointer. The flat published
  `chunk/` and `manifest/` directories must be real directories; symlinks are refused.
  Object and pointer leaves are held to the same rule, including same-root symlinks.
- **Fail-closed retry:** signature, hash, path, size, rollback, fetch, or lock failures do
  not promote the candidate. Verified inactive chunks may remain and are safely reused.
- **Concurrent mutation:** one cross-process lock covers fetch/verify/promote versus GC and
  makes the monotonic check/write indivisible. The default wait is 30 seconds; timeout is a
  typed `IntegrityError` and the caller should retry with jitter.
- **Local vector state:** `FaissVectorIndex` serializes insert, rebuild, delete, search,
  and statistics per instance. Persistence is also cross-process: save, writable load,
  legacy migration, and snapshot GC share one bounded file lock. A save writes
  generation-addressed FAISS and state files, flushes them, then publishes
  one atomic manifest commit and durable parent-directory links. Digests stream in fixed-size
  blocks, so verification does not copy a multi-gigabyte FAISS file into Python memory. Stable
  read-only loads do not take that lock: they enumerate a stable manifest set, pin open
  descriptors for the selected generation, verify both digests from those handles, and retry
  if a concurrent writer or GC changes the set. If the directory is an immutable legacy 0.4.0
  pair, a structurally valid pair loads directly without creating `snapshots/`, migrating,
  deleting, or otherwise writing. Every generation load accepts only a digest-matched complete
  commit. The read-only path makes three attempts to observe a stable manifest set, then fails
  closed with an operational `ValueError` if churn continues; it never returns a hybrid
  generation. An instance loaded from a generation also compare-and-swaps its
  selected generation during save: if another process committed first, save raises
  `SnapshotConflictError`; reload, reapply the intended mutation, and retry. It
  recovers the previous complete generation when the newest commit is corrupt, retains only
  those two generations, and migrates a valid 0.4.0 two-file directory on first load. A
  post-commit cleanup failure emits a warning log but does not misreport the already-active
  save as failed; the next save retries cleanup. Snapshot roots and internal directories must
  be real directories—symlinks and non-directories are refused before any snapshot write.
  Committed manifest, FAISS, and state leaves must be regular files; symlinks are never read.
- **Resource ceilings:** defaults are a 30-second HTTP client timeout per network
  operation, 256 MiB per response, 64 MiB decompressed per chunk, 4 GiB and 100,000 files
  per sync, 256 MiB per materialized file, and 30-second mutation and vector-snapshot lock
  waits. Total sync time still scales with the
  signed chunk count and origin latency. Operators should lower these limits for smaller
  catalogs and place a host-level deadline around the command when they require one.
- **Materialization:** CAS activation is atomic; writing a multi-file
  `--materialize-to` directory is not a crash-atomic directory swap. Consumers needing
  that property should materialize to a versioned staging directory, validate it, then
  atomically repoint their own symlink/directory reference.
- **Recovery:** retry sync after transport or lock failure. If the active manifest/chunks
  fail integrity, quarantine the cache, recreate it, and sync from a trusted origin/key.
  Reclaim disk with `edgeproc gc --cache-dir <cache>` (or `FilesystemCacheStore.gc()` when
  embedding the library). Either path takes the store's mutation lock, so GC is serialized
  against a concurrent sync rather than racing it, and a store with nothing promoted is
  left untouched — a no-op, never a wipe. Never delete objects out of a cache by hand.

EdgeProc has no independent uptime SLA because it is an embedded library. The host owns
origin redundancy, retry policy, alerting, disk monitoring, model warm-up, process
supervision, and end-user SLOs. `MemoryManager` enforces the sum of declared in-flight
task reservations for one `EdgeProc` instance and releases each reservation on every exit
path. It is admission control, not a portable native-RSS limit: the host must still set a
process/container memory limit and supervise FAISS, NumPy, model loading, and other native
allocations. Share one manager across facades that share a process boundary.

## Key rotation and compromised-key runbook

A consumer pins a **trust root** (`EDGEPROC_TRUST_ROOT_PUBKEY_PATH`, or `--key` on
`edgeproc sync`). It is one of two things, told apart by size:

- a raw 32-byte `public.key` — what `edgeproc keygen` writes. It loads as a keyring of one
  and verifies exactly as the single pinned key always did; nothing about an existing
  deployment changes; or
- a JSON **keyring** (`edgeproc.keyring/v1`, at most 64 KiB): 1–64 keys, each named by its
  `key_id` (the first 16 lowercase hex chars of the SHA-256 of the raw public key), plus a
  `revoked` list of up to 1024 key ids. A key listed in both is revoked. `edgeproc keyring
  init | add | revoke | show` maintains the file; `edgeproc keygen` prints each new key's id.

The signed `VersionPointer` may carry two optional signed fields. `key_id` names the key
that signed it: a keyring verifies it under THAT key only, refuses it as revoked
(`KeyRevokedError`) when the id is revoked, and as unknown (`UnknownKeyError`) when the ring
does not hold it. A pointer without `key_id` must verify under at least one non-revoked
key, so a revoked key's signature never verifies either way. `expires_at` (Unix seconds)
makes `sync` refuse the fetched pointer from that second on (`PointerExpiredError`); it is
judged only after the signature verified. Every refusal is `[bundle.integrity_failed]`,
fail-closed, and promotes nothing: what is already active stays active and keeps serving.

The anti-rollback floor is unchanged and independent of keys. The promoted pointer is the
floor, is never re-verified against a newly pinned trust root, and a pointer must carry a
strictly greater `sequence` to replace it — whichever key signed either one. So an OLD
release re-signed by a new key, or an old key's pointer replayed during the overlap
window, is refused as a rollback.

### Upgrade order: consumers first, then stamping

Stamping `key_id`/`expires_at` is **off by default** (`publish --stamp-key-id`,
`--expires-in`, or `EDGEPROC_PUBLISH_STAMP_KEY_ID` / `EDGEPROC_PUBLISH_EXPIRES_IN`), and
unstamped output is byte-identical to earlier releases. A consumer older than the keyring
release rejects a pointer that carries either field (its pointer model forbids unknown
fields): the sync fails and nothing is promoted, but it cannot update until upgraded.

1. Upgrade every consumer (Python hosts and `@edgeproc/browser` apps) to a keyring-aware
   release. Their existing raw `public.key` keeps working unchanged.
2. Optionally move consumers from `public.key` to a keyring file (`edgeproc keyring init
   keys/public.key --out keyring.json`); the ring of one behaves identically.
3. Only then turn on stamping at the publisher.

### Planned rotation (overlap window)

1. On the publisher, generate the new pair offline: `edgeproc keygen --out <new-dir>` (note
   the printed `key_id`). Keep `private.key` out of every repository and CI log.
2. Add the new public key to the consumers' keyring and ship it: `edgeproc keyring add
   keyring.json <new-dir>/public.key`. Consumers now trust `{old, new}`; the publisher is
   still signing with the old key, so nothing else changes yet.
3. Once every consumer has the `{old, new}` ring, switch the publisher to the new key:
   `edgeproc publish ... --key <new-dir>/private.key --sequence <N+1> --stamp-key-id`,
   where `N` is the live pointer's `sequence` and the `--bind-identity`/`--channel` values
   stay what consumers pin. Never restart the counter: a floor at `N` refuses anything at
   or below it, and an equal sequence that is not byte-identical is refused too.
4. Retire the old key: `edgeproc keyring revoke keyring.json <old-key-id>` and ship the
   `{new, revoked old}` ring. Keeping the revoked key's public half in `keys` lets a
   consumer report an unnamed pointer signed by it as revoked rather than merely invalid.
5. Destroy the old private key once nothing publishes with it.

There is no flag day: at every step each consumer holds a ring that verifies whatever the
publisher is currently signing.

### Compromised signing key

A holder of the private key can sign any pointer, at any `sequence`, with any
`expires_at`. Revocation is what stops consumers trusting it, and it takes effect for a
consumer when that consumer's pinned keyring is updated — there is no remotely fetched
revocation list in this library.

1. Revoke immediately: `edgeproc keyring revoke keyring.json <compromised-key-id>` (add the
   replacement key first if the ring holds no other key — a ring must keep one
   non-revoked key) and push the ring to every consumer as a configuration change. From
   that moment a consumer refuses every pointer the compromised key signed — named or
   unnamed, at any `sequence`.
2. Stop publishing with the compromised key and preserve the origin's current state for
   investigation.
3. Publish a known-good release with the replacement key at a `sequence` strictly greater
   than any value the attacker could have pushed to consumers (bump well past it). An
   attacker who pushed a very large `sequence` leaves those consumers with a floor no
   legitimate release can clear; they must recover as in step 4.
4. Treat every consumer that synced while the key was exposed as possibly holding attacker
   content. Quarantine and recreate its cache (see the recovery contract above; in the
   browser, the explicit cache clear), then sync from the trusted origin under the new
   ring. EdgeProc has no telemetry, so which consumers synced in the window is the host
   operator's record, not the library's.

A pre-provisioned standby key shortens step 1: consumers that already pin `{active,
standby}` only need the revocation pushed, and the publisher can switch to the standby key
at once.

### Expiry and the re-sign cadence

`expires_at` bounds how long a withheld pointer stays acceptable: an attacker who can block
updates (a freeze) can keep a consumer on its last valid release only until that release's
pointer expires. Choose `--expires-in` longer than your longest publish interval plus the
slowest consumer's sync interval and clock skew, then re-sign on a fixed cadence — for
example `--expires-in 7d` and a daily publish. Re-signing an unchanged release still needs a
strictly greater `--sequence`. When a consumer's latest fetched pointer has expired, `sync`
exits non-zero and the consumer keeps serving what it already has; that is the signal to
alert on. Consumer clocks matter: a clock running ahead expires pointers early (a refusal,
never an acceptance); a clock set far behind extends the freeze window.

### Known limits

- **Revocation is local configuration.** It protects a consumer from the moment its pinned
  keyring lists the key as revoked, and not before. Distribute keyring updates the way you
  distribute the trust root itself.
- **Expiry bounds a freeze, not a compromise.** A holder of a still-trusted key can sign
  pointers with any expiry; revocation is the answer to compromise.
- **A floor can be pinned too high.** A tampered durable counter, or an attacker-signed
  huge `sequence`, makes the consumer refuse every later release until its cache is
  cleared. The failure is a refusal, never an accepted rollback.
- **Stamped pointers need upgraded consumers.** See the upgrade order above.

## Measured performance contract

Run the fixed, offline benchmark:

```bash
uv run python benchmarks/benchmark.py
```

The fixture and budgets are set before measurement: 10,000 normalized 32-dimensional
vectors with 30 searches after warm-up; and a signed 4 MiB bundle with seven cold syncs
plus 20 no-change syncs. The gate requires vector-search p95 <= 100 ms, cold-sync p95
<= 750 ms, warm-sync p95 <= 250 ms, and process max RSS <= 512 MiB. The script prints
JSON with p50, p95, maximum, RSS, fixture sizes, Python/platform identity, and pass/fail.

These numbers cover library-owned FAISS lookup and signed filesystem sync without network
variance. They deliberately exclude model download/encoding and CDN latency, which depend
on the consumer's model, hardware, and origin and must be measured in the embedding app.

### Measured evidence

**This table is the single source for EdgeProc's performance figures.** They are stated
here and nowhere else — no other document restates them, so there is nothing to drift.

Measured 2026-08-13 on macOS 26.5 arm64 (Apple silicon laptop), CPython 3.13.5,
from the 0.4.1 release candidate after the full local gate:

| metric | p50 | p95 | gate budget |
|---|---|---|---|
| vector search | 0.065 ms | 0.084 ms | 100.0 ms |
| cold sync | 51.494 ms | 51.936 ms | 750.0 ms |
| warm sync | 14.838 ms | 17.226 ms | 250.0 ms |

Peak process RSS was 116.75 MiB against the 512 MiB budget.

Read the p95 column as a shape, not a constant. Cold sync is the noisiest metric: two
consecutive runs on this same machine can vary materially, because seven cold
syncs give the 95th percentile very few samples and each one is dominated by filesystem
behavior the library does not control. The table records the slower run deliberately —
an optimistic number is the more dangerous error. This is also why the drift test in
`tests/test_release_contract_docs.py` compares these committed figures against the
committed budgets rather than against a benchmark run at test time: a test that raced a
live measurement against a documented one would fail on machine variance, not on defects.

These measurements describe that exact tree and machine. They are not a promise for
every consumer, and the budgets above — not these figures — are what the gate enforces.

## Release evidence

A release requires a fresh manual `workflow_dispatch`; pushing a tag never publishes by itself.
Dagger first requires that the requested commit is the exact current `main` commit with a green
hosted `Dagger` check. It fetches that immutable commit itself, verifies exact
tag/version/top-changelog identity. Dagger runs all five checks: `uv run poe gate`, the real example,
the benchmark, the locked dependency audit, and both the exact snapshot and full Git history
secret scans. Dagger builds the wheel and sdist without build isolation, validates their project
and version metadata, and records literal SHA-256 identities. Record the immutable commit/tag and
benchmark JSON; do not infer production truth from a different local tree.

Dagger builds and validates in an unprivileged job and exports one exact short-lived candidate.
A pinned artifact-upload action is the only bridge out of that job. Its successful manual run
triggers `publish.yml` from protected default-branch code. That source-free OIDC-bearing job only
invokes the central lineage proof, pinned artifact download, and official PyPI publish actions
against the exact triggering run; it has no checkout, shell, dependency install, build backend, or
project code execution. The lineage proof is `portfolio-foundation`'s `release-lineage`, pinned at a
literal `hseshadr/ci` commit. It runs first and fails unless GitHub's run records show a successful
`release-candidate.yml` dispatch here for exactly the candidate SHA, and that `main` contains it: the
job's `head_branch` check alone also passes for a dispatch on a tag named `main`. The manual dispatch
is the fresh publication authorization.

The local `poe gate` is only one portion of the hosted Dagger graph. Hosted pull requests pass
their exact commit SHA so Gitleaks scans every ancestor introduced by the branch, including a
secret added and later deleted before the tip. See the "no key material in the tree" invariant
in `CLAUDE.md`.
