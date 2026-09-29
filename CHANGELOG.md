# Changelog

All notable changes to **edge-proc**. Newest first; we follow [SemVer](https://semver.org).

## [Unreleased]

### Documentation

- **The README is rewritten in plain English.** It now opens with what EdgeProc is and a
  runnable example with real output, then how it works, how it relates to `edgeproc-core`,
  `@edgeproc/browser`, edge-reco and privacy-core, and its limits. Technical material moved
  out, not away: the configuration table to the new `docs/CONFIGURATION.md`; the trust
  model, key rotation, router and budget notes to `docs/ARCHITECTURE.md`; the Python API
  example to `docs/QUICKSTART.md`. New `docs/GETTING_STARTED.md` takes a developer from a
  fresh clone to a green build and a first change. The README contract tests now pin the new
  section order and reject internal jargon. The package description changes to match the
  README's first line; PyPI shows it from the next release.
- **`docs/QUICKSTART.md` no longer claims `poe gate` takes ~20 s.** It took about
  3.5 minutes from a fresh clone.

### Changed

- **The release candidate is back to the fleet's shell-free shape: checkout, Dagger,
  upload.** The central `hseshadr/ci` fleet policy forbids `run:` steps and reported
  `shell-step` and `candidate-order` on `release-candidate.yml`. The 0.5.0 hardening kept
  the dispatched tag out of script text by moving the call into `run:` steps. It now stays
  out of script text inside the pinned `dagger/dagger-for-github` step instead: the tag
  reaches it only as the `TAG` environment variable, and `args` hold only double-quoted
  variables (`--tag="$TAG" --commit-sha="$GITHUB_SHA"`), so bash expands the tag as one
  inert word. The separate `vX.Y.Z` shell guard is gone. Dagger already rejects any tag
  that differs from the metadata-derived release tag. `tests/test_workflow_security.py`
  now expands the real `args` in bash with hostile tags and asserts that each one arrives as
  a single literal argument and runs nothing.

## [0.5.0] — 2026-09-23

This minor release adds a trust-root keyring, so a publisher can rotate or revoke its
signing key without a hard cutover, and makes pointer expiry available. It is additive:
a consumer that pins a raw `public.key` and a publisher that does not opt into stamping
behave exactly as on 0.4.1, and every pointer they exchange is byte-identical. It also
raises the `edgeproc-core` floor to 0.4.3 and hardens the manual release workflow.

### Upgrading

- **Nothing to change for existing single-key deployments.** A raw 32-byte `public.key`
  trust root keeps working unchanged through `EDGEPROC_TRUST_ROOT_PUBKEY_PATH` or
  `sync --key`: it loads as a keyring of one and verifies exactly as before.
- **Pointers are unchanged unless you opt in.** `key_id` and `expires_at` are optional.
  When they are unset, the signing preimage, the signature, and the published `latest`
  bytes are byte-identical to 0.4.1 output (pinned by
  `tests/bundles/test_pointer_keyring.py` and re-checked against the v0.4.1 sources for
  this release). So a 0.5.0 consumer syncs a 0.4.x publisher's bundles, and a 0.4.x
  consumer keeps syncing a 0.5.0 publisher that leaves stamping off (the default).
- **Upgrade every consumer before you turn stamping on.** `publish --stamp-key-id`,
  `publish --expires-in`, `EDGEPROC_PUBLISH_STAMP_KEY_ID`, and
  `EDGEPROC_PUBLISH_EXPIRES_IN` add signed fields that a 0.4.x consumer refuses. The order
  is in the key rotation runbook in `docs/OPERATIONS.md`.
- **`edgeproc keygen` prints a second line** (`key_id <16 hex chars>`). A script that parses
  its output should read only the first line, or match on the `wrote` prefix.
- **`edgeproc publish` now validates `EDGEPROC_*` settings.** A malformed value that
  `publish` used to ignore is now refused as `config.invalid`, the same way `sync` already
  handled it.
- **`edgeproc-core>=0.4.3` is now required**, so installing 0.5.0 upgrades an older core.
  No code change is needed; see *Security* below for why the floor moved.

### Added

- **Trust-root keyring.** `EDGEPROC_TRUST_ROOT_PUBKEY_PATH` / `sync --key` now accept a
  JSON keyring (`edgeproc.keyring/v1`: 1–64 keys named by `key_id`, up to 1024 revoked
  ids, at most 64 KiB) as well as the raw 32-byte `public.key`, which loads as a keyring of
  one and verifies exactly as before. A `key_id` is the first 16 lowercase hex chars of
  the SHA-256 of the raw public key. Parsing is strict: unknown fields, another schema, a
  `key_id` that does not match its key, duplicates, or a ring with no unrevoked key are
  refused. Library surface: `edgeproc.bundles.keyring` (`KeyringVerifier`,
  `load_trust_root`, `parse_keyring`, `keyring_of`, `add_key`, `revoke_key`,
  `keyring_bytes`) and `edgeproc.bundles.signing.key_id_for`.
- **Two optional signed pointer fields.** `VersionPointer.key_id` names the signing key and
  `VersionPointer.expires_at` (Unix seconds, an integer below 2^53) bounds how long the
  pointer is accepted. Both fold into the signing preimage only when set and are omitted
  from the serialized pointer when unset, so every existing pointer, its signature, and the
  published `latest` bytes are byte-identical.
- **Opt-in publisher stamping:** `edgeproc publish --stamp-key-id` and
  `--expires-in <seconds | 90s | 30m | 12h | 7d | 2w>`, with the settings
  `EDGEPROC_PUBLISH_STAMP_KEY_ID` (default off) and `EDGEPROC_PUBLISH_EXPIRES_IN` (default
  none); `build_bundle(key_id=..., expires_at=...)` in the library. Upgrade every consumer
  before enabling either: an older consumer refuses a pointer carrying them.
- **`edgeproc keyring init | add | revoke | show`** to create and maintain a keyring file
  (canonical, sorted output; `init` never overwrites, edits are atomic and refuse
  symlinks). `edgeproc keygen` prints the new key's `key_id` on a second line.
- `sync_index(clock=...)` (Unix seconds, default `time.time`, read only for a pointer that
  carries `expires_at`) and `edgeproc.bundles.sync.verify_pointer`, the pointer-level
  checks sync applies (key selection, signature, then expiry).
- **Cross-runtime vectors** from fixed seeds, generated by `scripts/gen_keyring_vectors.py`:
  `tests/fixtures/keyring_vectors.json` is byte-identical to `@edgeproc/browser`'s copy,
  and `tests/fixtures/keyring_scenarios.json` drives rotation, rollback-floor, and
  precedence cases through complete syncs.
- **Key rotation and compromised-key runbook** in `docs/OPERATIONS.md`, linked from
  `SECURITY.md`: the upgrade order (consumers first, then stamping), the overlap-window
  rotation (`{old, new}` → switch the publisher → `{new, revoked old}`), the
  compromised-key path (revoke immediately, bump `sequence`), and the expiry re-sign
  cadence, with their limits stated.
- A regression test pins that the promoted pointer stays the rollback floor after the
  pinned key changes: an old release re-signed by a new key is refused, and a fresher one
  promotes. `@edgeproc/browser` was fixed to hold the same rule.

### Security

- A revoked key's signature never verifies, at any `sequence`, whether or not the pointer
  names its key (`KeyRevokedError`). A pointer naming a key the trust root does not hold is
  refused (`UnknownKeyError`); a named key is the only key tried. Both subclass
  `SignatureError` and carry `bundle.integrity_failed`.
- A fetched pointer at or past its signed `expires_at` is refused (`PointerExpiredError`,
  an `IntegrityError`), judged only after the signature verified, so an attacker who
  withholds updates can freeze a consumer only until that pointer expires. Nothing is
  promoted; the active bundle keeps serving.
- The `sequence` rollback floor is unchanged and holds across key changes: an old key's
  pointer replayed during a rotation overlap is refused as a rollback.
- **The `edgeproc-core` floor is now 0.4.3.** `edgeproc.errors.problem_details_for(error,
  params)` renders through core's `to_problem_details`. Before 0.4.3, caller params could
  put reserved RFC 9457 members (`status`, `detail`, `instance`) on the wire, along with
  `__proto__`, non-scalar or non-finite values, and a `str`-subclass key that forged
  `status` and a duplicate `type`. 0.4.3 keeps only wire-safe members, so edge-proc now
  refuses to install with a core that lacks that fix.
- **The manual release workflow no longer pastes the dispatched tag into shell text.**
  `release-candidate.yml` passed `--tag=${{ inputs.tag }}` in the `args` of
  `dagger/dagger-for-github`, which interpolates `args` into bash unquoted. A crafted
  `workflow_dispatch` tag could therefore run commands on the runner and upload its own
  wheel for `publish.yml` to publish. The tag now reaches shell only as the `TAG`
  environment variable. A separate step rejects anything but a plain `vX.Y.Z` before any
  other shell runs, the pinned action only installs the Dagger CLI, and the release call
  runs in a `run:` step with every value quoted. `tests/test_workflow_security.py` now
  fails if any workflow pastes a `${{ inputs.* }}`, `${{ github.event.* }}`, or
  `${{ github.head_ref }}` expression into a `run:` body or a Dagger action input. It also
  runs the tag guard against injection payloads. `publish.yml` was already shell-free and
  names its upload directory explicitly (`packages-dir: release/dist`).
- **The locked `anyio` moved from 4.13.0 to 4.14.2** (#71), which clears CVE-2026-63374
  (GHSA-82r6-8w77-94w6, TLS hostname checks for internationalized domain names) and
  CVE-2026-64847 (GHSA-5p39-cfhj-2xmp, an undrained process-pool stderr pipe). `anyio`
  reaches edge-proc only through `httpx` in the `[bundles]` extra, and edge-proc declares
  no `anyio` floor of its own. This bump therefore protects the committed lock that CI,
  the release build, and `uv sync` users install. A pip install resolves `anyio` itself,
  so upgrade it to 4.14.2 or later there.

### Changed

- `edgeproc publish` now reads `EdgeProcSettings` for its stamping defaults, so a malformed
  `EDGEPROC_*` value in the environment is refused as `config.invalid` instead of being
  ignored by `publish` (as `sync` already did).
- The README was rewritten (#73) to follow the portfolio template: a plain-language first
  screen, a runnable offline example with its real output, and an "At a glance" summary.
  The package description is now that tagline, and the classifier is `Development Status
  :: 4 - Beta`. `tests/test_readme_contract.py` pins the first screen, and the source
  archive now ships the files the README links to (`CONTRIBUTING.md`,
  `.github/ISSUE_TEMPLATE/`).
- **CI and releases run through one Dagger graph** (#54, #58, #61, #62). Pull requests and
  `main` call the same graph that `poe gate` mirrors. A release candidate still needs a
  fresh manual `workflow_dispatch`, and it now also needs an existing `vX.Y.Z` tag that
  names the exact current, green `main` commit and matches the package version and the top
  released CHANGELOG entry. The OIDC publish job stays source-free and shell-free
  (`docs/OPERATIONS.md#release-evidence`).
- An interactive, fully offline architecture map (`docs/architecture/index.html`) is linked
  from the README (#67). The dependency audit now also covers wheels from outside PyPI
  (the CPU `torch` index) through OSV, and the locked `transformers` moved from 5.9.0 to
  5.10.1 to clear the advisory that audit reported (#67).
- Routine dependency refresh of the committed lock (#70, 14 updates in the `python-deps`
  group) and of the pinned CI actions (#69).
- `ROADMAP.md` and the README now describe the keyring as shipped in 0.5.0, and
  `SECURITY.md` supports 0.5.0 only.

## [0.4.1] — 2026-08-13

This corrective release supersedes 0.4.0 for applications that persist a
`FaissVectorIndex`. Upgrade before the next index save/load cycle.

### Fixed
- **A local vector snapshot can no longer combine files from different saves.** The FAISS
  binary and its metadata sidecar now live under a unique generation. Both are flushed before
  one atomic manifest commit makes that generation readable. Save, writable load, migration,
  and snapshot cleanup share one bounded cross-process lock; an interrupted save is ignored,
  the previous complete generation remains recoverable, and retention is bounded to those
  two generations. Loaded writers compare-and-swap their generation so a stale process
  cannot erase a newer commit; file verification streams with bounded memory;
  snapshot-directory symlinks are refused. Cleanup after an active commit is best-effort and
  observable, so a cleanup failure cannot falsely report that the committed save failed.
  Valid writable 0.4.0 index directories migrate on first load.
- CAS atomic writes now flush every newly created parent and shard plus the directory after
  `os.replace`, so new directory entries—not only file contents—survive a power-loss boundary
  on durable filesystems.
- Published-origin chunk links are flushed before the durable `latest` pointer; copy fallback
  also flushes each destination file so `latest` cannot outlive the objects it references.
  Symlinked flat-origin chunk or manifest directories are refused before publication.
- Snapshot, CAS, pointer, and flat-origin object leaves now refuse symlinks even when the
  target stays inside the selected root; no trusted pointer advances over an aliased object.
  CAS garbage collection refuses symlinked chunk shards and object leaves before sweeping,
  so cleanup cannot unlink a file outside its cache. The publisher refuses symlinks under
  `--src`, so a source-tree alias cannot pull an external file into a signed release.
- Read-only saved indexes load without writing their snapshot directory. Writable loads
  retain the cross-process snapshot lock; stable read-only loads do not take the snapshot
  lock. They pin open descriptors for one stable manifest enumeration, verify both files
  through those handles, and retry up to three times if concurrent cleanup changes the
  manifest set before failing closed. Immutable legacy pairs load directly without migration
  or deletion. CLI permission failures now render a coded refusal.
- Publishing verifies and repairs reused chunk objects before advancing `latest`. A corrupt
  producer-side CAS object or flat-origin hardlink is atomically replaced from the current
  source bytes, so a successful re-publish remains syncable by a fresh device.
- Trusted publishing now builds, tests, audits, and validates archives without OIDC permission.
  The credentialed job only downloads the short-lived artifact, rechecks its SHA-256 digests
  and metadata without executing package code, and invokes the official PyPI publisher;
  registry propagation verification runs afterward without OIDC.
- The source distribution now includes the benchmarks, operational docs, workflow fixtures,
  mutation harness, environment example, citation, roadmap, contributor guide, and lockfile
  required by its shipped test suite. Its contract tests run from the extracted archive.
- The supported `edgeproc-core` floor is now 0.4.2; superseded core releases are excluded
  from built package metadata.

## [0.4.0] — 2026-08-12

> **Superseded by 0.4.1** for persisted `FaissVectorIndex` state. The offline model contract
> below remains current, but applications that save local vector indexes should upgrade.

This release makes the documented cold-device offline contract installable. It is a
breaking release because implicit model downloads now refuse by default.

### Fixed
- **"Works offline" was false on a cold device, and is now true.** The README promised that
  "after one sync the device needs no network at all to keep answering queries". `sync`
  shipped the FAISS index but not the *embedding model*, so `TextEncoder` resolved
  `sentence-transformers/all-MiniLM-L6-v2` by calling huggingface.co when it was
  constructed. On any machine with a warm Hugging Face cache that fetch is invisible, every
  test passed, and the claim read as true. Reproduced on a genuinely cold cache: the
  encoder issued `HEAD https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/
  resolve/main/./modules.json` and the query could not be answered.

  `docs/OPERATIONS.md` was the only file that stated this correctly. `README.md` and
  `docs/ARCHITECTURE.md` contradicted it, including ARCHITECTURE's claim that the pinned
  public key "is the only thing the consumer has to obtain out-of-band" — the model was a
  second out-of-band artifact, and unlike the key it was neither pinned nor verified.

### Added
- **Fail-closed model resolution** (`edgeproc.localvec.model_source`). Egress is now opt-in
  rather than a fallback. With no local model configured, `TextEncoder()` raises
  `ModelNotLocalError` (canonical code `config.missing`) **before any loader is
  constructed** — it does not fetch. The ordering is the guard: `huggingface_hub`
  downloads through `hf_xet`, a Rust extension that never enters Python's `socket` module,
  so a socket-level block cannot observe it. Refusing before the call is the only check
  that cannot be bypassed from underneath.
- `EDGEPROC_MODEL_PATH` — a local model directory, the supported offline path. Ship the
  model under `publish --src` and point this at what `sync --materialize-to` wrote, and the
  weights travel under the same pinned key as the data.
- `EDGEPROC_MODEL_DIGEST` — optional sha256 pin over that directory for a model that did
  *not* arrive through `sync`. A mismatch is refused (`bundle.integrity_failed`), not warned.
- `EDGEPROC_ALLOW_MODEL_DOWNLOAD` (default `false`) — permits a one-time fetch. Intended
  for a build machine; a device never sets it.
- `TextEncoder.save(path)` — writes the loaded model so `publish` can chunk and sign it.
  This is the provisioning half that makes the fail-closed default workable.
- `edgeproc route --model-path`. A model refusal now renders as a coded CLI failure
  (`[config.missing] ...`) instead of a traceback.

### Changed
- **BREAKING for anyone relying on the implicit download.** `TextEncoder()` previously
  fetched the model on demand; it now refuses unless `EDGEPROC_MODEL_PATH` is set or
  `EDGEPROC_ALLOW_MODEL_DOWNLOAD=1`. The refusal names both remedies verbatim, so the
  migration is one environment variable. This is the same fail-closed posture `sync`
  already takes when no trust root is pinned.
- `examples/run_loop.sh` now demonstrates the invariant rather than merely surviving it.
  Step 2 is labelled the build machine and is the only step permitted to fetch; step 5
  routes with **every Hugging Face cache variable redirected at an empty directory**, so a
  passing search proves the weights came out of the verified bundle and not from the
  developer's cache; step 6 drops `--model-path` and asserts the refusal, so the guard is
  watched failing on every run.

## [0.3.1] — 2026-08-03

A dependency-metadata release, and it only matters once published. The floor
fixed on `main` reaches nobody: PyPI 0.3.0 still advertises `cryptography>=44`
to every installer, so a fresh `pip install edge-proc[bundles]` resolves to a
version affected by all three advisories below. No API change.

### Security
- **Raised the `cryptography` floor to `>=50` (was `>=44`).** `cryptography` is the library
  that performs edge-proc's fail-closed signature verification, so its floor is inherited by
  every project that installs edge-proc. Three advisories landed against it:

  | Advisory | Severity | Affected | First fixed |
  | --- | --- | --- | --- |
  | [CVE-2026-69247](https://github.com/advisories/GHSA-g6cj-pr64-35w5) — PKCS#7 `EnvelopedData` decryption leaks a Bleichenbacher oracle through distinguishable errors and timing | High (8.2) | `>=44.0.0, <50.0.0` | **50.0.0** |
  | [CVE-2026-69248](https://github.com/advisories/GHSA-m2h6-j472-rp4c) — X.509 verifier accepts a wildcard SAN escaping an intermediate's `permittedSubtrees` | Moderate (6.9) | `<=48.0.0` | 49.0.0 |
  | [CVE-2026-69249](https://github.com/advisories/GHSA-jwv3-5hgf-82ww) — duplicate self-signed intermediates cause exponential path building (DoS) | High (8.7) | `<=48.0.0` | 49.0.0 |

  `>=44` resolved onto all three; 49.0.0 clears only the last two, so the floor is 50.

  **edge-proc itself is not on any of the three affected code paths.** It uses raw ed25519
  (`Ed25519PrivateKey` / `Ed25519PublicKey` and `InvalidSignature`) and imports nothing else
  from `cryptography` — no `pkcs7_decrypt_*`, no `x509.verification`, no certificate path
  building. All three advisories are confined to PKCS#7 decryption and X.509 chain
  verification. The floor is raised regardless: a project that installs edge-proc may reach
  for those surfaces itself, and a dependency's floor is the weakest version it permits, not
  the version it happens to resolve today.

  **Floor only — deliberately no ceiling.** A cap on a security-critical dependency turns the
  next major-delivered CVE fix into a blocked build. Guarded by
  `tests/test_dependency_floors.py`, which fails on a lowered floor *and* on any added cap.

## [0.3.0] — 2026-08-03

A security release. Two anti-rollback paths accepted a promote they could not prove
was fresh, and one of them is reachable by replaying a genuinely-signed pointer — no
forgery required. **Breaking:** a publisher re-shipping under one version label now
needs a strictly-greater `--sequence`.

### Fixed
- **Anti-replay failed OPEN on an EQUAL version — a promote now needs a *strictly greater*
  one.** `Version(incoming) < Version(active)` is `False` when the two versions are equal,
  and the guard read that single `False` as affirmative proof of freshness. Two different
  bundles can wear one version label, so an equal version says nothing about which is
  newer. Measured before the fix: a device on the re-published `1.2.0` bundle accepted the
  earlier, genuinely signed `1.2.0` pointer, and its content moved backwards under an
  unchanged version string. Nothing was forged — a replayed pointer is validly signed by
  construction, so "I cannot tell whether this is a rollback" must REJECT.

  **Breaking, and deliberately so.** A publisher that re-ships under one version label now
  needs a strictly-greater monotonic `sequence` (`--sequence`) to prove freshness.
  `sequence` was already the documented escape hatch for versions PEP 440 cannot parse; it
  is now the escape hatch for equal versions too. A first promote, a forward version bump,
  and a byte-identical re-promote (idempotent no-op) are all unaffected. `RollbackError`'s
  message now names *strictly greater* as the bar.

- **An unreadable `active` pointer skipped the anti-rollback guard entirely.**
  `read_active` tested `is_file()`, so an `active` that existed but was not a regular file
  answered "nothing has ever been promoted" — the one answer that tells the guard it has
  nothing to be fresher than. The promote then needed no proof at all, and the only thing
  that stopped it was the filesystem refusing the swap. An `active` that exists but cannot
  be read as a pointer is now a catalogued `IntegrityError` (`bundle.integrity_failed`)
  rather than a raw pydantic `ValidationError` escaping every `except IntegrityError`
  fail-closed handler. `None` is reserved for a store with no `active` entry at all.

### Added
- Tests for nine previously unwitnessed refusal branches — `raise` paths no test had ever
  executed, across the sync engine, the memory admission guard, and the persisted FAISS
  index validator.

## [0.2.0] — 2026-08-01

### Fixed
- **Anti-rollback and anti-replay now fail CLOSED.** Both promote-time freshness guards
  could be bypassed by supplying *less* information rather than better credentials:
  - A `version` string PEP 440 cannot parse made the anti-rollback guard swallow
    `InvalidVersion` and answer "not a downgrade", so any publisher using date-style
    versions had no anti-rollback at all. Measured before the fix: a genuinely signed
    `2019-01-01` pointer replaced an installed `2026-07-01` one.
  - A missing `sequence` made the anti-replay guard answer "fresh", so deleting the
    counter from a validly signed older pointer defeated it. Measured before the fix: a
    device on `sequence=7` accepted an unsequenced pointer at an equal version, where the
    PEP 440 guard never speaks.

  `promote` now requires PROOF that the incoming pointer is at least as fresh as the
  active one — a strictly-greater monotonic `sequence`, or a comparable PEP 440 `version`.
  Either comparison proving staleness refuses; *neither* comparison being able to speak
  also refuses. A first promote has nothing to be fresher than and is unaffected, a
  byte-identical re-promote stays idempotent, and a publisher whose versions PEP 440
  cannot parse keeps shipping by binding `--sequence`. `RollbackError` now names which
  proof failed.

  This reverses a covenant the project previously held ("the guard must never reject a
  validly-signed bundle"): a signature proves *authorship*, never *freshness*, and a
  replayed pointer is validly signed by construction.

- **The README said `edge-proc` "isn't on PyPI yet" thirteen lines above `pip install
  edge-proc`.** It has been on PyPI since 2026-07-22 (0.1.5). A reader who believed the first
  line stopped there and never ran the quickstart. Install now leads with PyPI, and the
  clone-and-go path is framed as what it is: for running the walkthrough or hacking on
  EdgeProc. Added a PyPI version badge so the claim is checkable at a glance.
- **The quickstart's stated cost was wrong and its shape was unstated.** "About 200 MB free"
  was off by 5x — measured from a fresh clone into a fresh venv with cold caches, the real
  cost is ~1.0 GB (947 MB venv, torch and FAISS dominating, plus an 87 MB `all-MiniLM-L6-v2`
  download) and about one minute of machine time. Both READMEs now print the measured table.
  They also say the shape out loud: one 30-line Python script, then the CLI. The script is not
  ceremony — no `edgeproc build-index` verb exists, because persisting an index is library
  work. Dropped the "five-minute" framing in favor of the numbers.

### Changed
- **Architecture diagrams are now inline mermaid, not committed SVGs.** The three d2 sources
  under `docs/diagrams/` and their rendered SVGs are gone; the diagrams live as `mermaid`
  code fences inside `docs/ARCHITECTURE.md`, next to the prose that explains them. d2 emitted
  roughly 4:1 letterboxed images that shrank to ~160 px tall in a GitHub-width column, which
  made the labels unreadable. Mermaid renders at a legible height, has no build artifact that
  can go stale against the text, and shows up as a readable diff in review.
- **`is_fresh_sequence` is fail-closed on an absent counter.** Once the active pointer
  carries a `sequence`, an incoming pointer that carries none is no longer "fresh". An
  active pointer with no counter stays undecidable — there is no counter state to roll
  back to — so a pre-sequence store remains upgradable and PEP 440 decides there.

- **BREAKING: `FaissVectorIndex` now REFUSES an index option it cannot implement.**
  It previously accepted every `IndexConfig` knob and honoured exactly one of them.
  `distance_metric="l2"` was measured building a `faiss.IndexFlatIP`
  (`metric_type=0`, inner product) with no error and no warning — the caller believed
  they had configured Euclidean distance and silently got a different number.
  `ef_search`, `m`, and `ef_construction` were stored on `self.config` and never read by
  any code path; `search(..., ef_search=64)` was measured returning results identical to
  `search(...)`. A wrong answer nobody can detect is worse than a refusal.

  The index now raises `UnsupportedIndexOptionError` — a `ValueError` carrying the
  canonical `config.invalid` code — on construction, `rebuild`, `load`, and `search`
  when asked for something it does not implement. It honours `dimension` and a
  `distance_metric` of `"cosine"` (inner product over unit-normalized vectors IS cosine
  similarity; the returned score is `1 - similarity`, i.e. cosine distance). The HNSW
  knobs are refused only when *changed* from their defaults, so a config that never
  tuned them is unaffected — which is every call site in this repo, the quickstart, and
  the README.

  Chosen over implementing the knobs because `IndexConfig` is shared-libs' deliberately
  wide pass-through type ("a backend is free to honour or ignore them"), `m` /
  `ef_construction` / `ef_search` have no meaning on a brute-force flat index at all,
  and an `l2` path over already-normalized vectors would fork the score conversion to
  deliver a monotone transform of the metric that is already there. EdgeProc sits at the
  head of the dependency spine, so a narrow honest surface beats a wide lying one.

  **Migration:** if you set any of these, drop them — they never did anything. To keep a
  saved index loadable, its `state.json` `config` must not name a metric other than
  `cosine`.

## [0.1.5] — 2026-07-21

First release published to PyPI as
[`edge-proc`](https://pypi.org/project/edge-proc/), so `pip install edge-proc`
(and its extras) now works directly.

### Added
- **`edgeproc gc` command.** The runbook told operators to reclaim disk through
  `FilesystemCacheStore.gc()`, but no CLI entry point existed — an operation documented
  with no way to run it. `edgeproc gc --cache-dir <cache>` sweeps every chunk and manifest
  the active pointer does not reference, behind the store's mutation lock, and is a
  no-op on a store with nothing promoted.
- **Canonical codes on every operator-facing failure.** `SignatureError` and
  `ResponseTooLargeError` now carry `bundle.integrity_failed` / `bundle.download_failed`,
  and the CLI stamps its fail-closed config refusals with `config.missing` /
  `config.invalid`. A test now fails if a declared code has no throw site.
- **Performance-claim drift guard.** A test compares the committed evidence table in
  `docs/OPERATIONS.md` against the committed budgets in `benchmarks/benchmark.py` —
  never against a benchmark run at test time, so it cannot flake on machine variance.
- **Canonical, portable integrity failures.** Bundle-integrity exceptions now
  carry the shared `bundle.integrity_failed` code and can be rendered as RFC
  9457 Problem Details without changing their existing Python type or message.
- **Evidence-backed operating contract and benchmark.** A single operations guide now
  defines threat/privacy boundaries, recovery ownership, fixed resource limits, and a
  repeatable offline p50/p95/RSS gate for vector search and signed bundle sync.

### Changed
- **`edgeproc-core` now installs from PyPI.** The `[tool.uv.sources]` git pin
  (commit `6cdf847`) is dropped; the dependency is a plain `edgeproc-core>=0.2.1`
  requirement resolved from PyPI, where upstream's `0.2.1` release is the first
  to ship the `edgeproc_core` import package. README and CONTRIBUTING
  onboarding prose follow, and the commented local path override remains for
  co-development.
- **Upstream dependency renamed `shared-libs-python` → `edgeproc-core`.** Imports move
  from `shared_libs_python.*` to `edgeproc_core.*`, and the dependency spec, the
  `[tool.uv.sources]` key, and the lock all follow. The upstream GitHub repository is
  unchanged, so the git URL still reads `hseshadr/shared-libs-python`; only the
  distribution and import names moved. The pin advances to the commit carrying the new
  name — still a full immutable SHA, never a mutable tag.
- **One implementation of sequence freshness.** `cas._sequence_violation` delegates its
  counter comparison to the public `is_fresh_sequence` predicate instead of keeping a
  private near-duplicate, so the two can no longer drift apart. Behavior is unchanged,
  including the idempotent same-sequence re-promote.
- **Performance figures have one home.** The README restated a cold-sync p95 of 55 ms
  that was actually that run's p50, while `docs/OPERATIONS.md` said 111.0 ms. Measured
  figures now live only in `docs/OPERATIONS.md`, with the hardware stated; the README
  links to them.
- **The shared error dependency is pinned by commit SHA, not tag.** A git tag is mutable,
  so a moved `v0.2.0` would have silently changed the dependency for every clone. Pinned
  to `ed1c3f6414710cb27d1c01e5fc2d6cadf0214b25`, the commit that tag pointed at.
- **The workflow-pinning audit scans `*.yaml` as well as `*.yml`** and asserts a non-zero
  action count, so a renamed workflow can no longer make the check pass vacuously.
- **`.env.example` documents the whole config surface.** It listed 3 of 15 settings
  fields; a test now fails if any field is undocumented.
- **`CITATION.cff` version tracked into the drift test.** It had sat at `0.1.1` through
  three releases; the existing version-drift test now covers it.
- **Task memory budgets fail closed at the typed boundary.** Non-positive
  `budget_memory_mb` values are rejected by `Task`; forged/unvalidated task
  instances receive an `invalid_memory_budget` failure envelope instead of
  leaking a raw `ValueError` from admission control.
- **The shared error dependency now resolves from its released v0.2.0 tag.**
  Fresh clones and CI consume the same immutable public release instead of the
  temporary pre-release commit pin.
- **Filesystem mutations are cross-process serialized and bounded.** Publish, sync,
  rollback-check/promote, GC, and CLI materialization share one mutation lock, closing
  stale-last-writer and sync-vs-GC races. Lock waits fail retryably after 30 seconds.
- **Task budgets are documented truthfully.** The v0 fields are runtime declarations,
  not facade-level preemption or whole-process memory enforcement.
- **Workflow actions are immutable.** CI, gitleaks, and scheduled dependency-audit
  actions are pinned to full commit SHAs, with a regression test that rejects moving tags.
- **Security lock refresh.** `setuptools` is locked at 83.0.0, clearing
  PYSEC-2026-3447 reported by the exact-branch dependency audit.

### Removed
- **`EdgeProc.local_default()`** — a zero-argument constructor whose empty registry
  refused every task, advertising a working default that could not work. Construct
  `EdgeProc(RuntimeRegistry())` explicitly.
- **`MemoryManager.reserved_bytes`** — a counter no production code read;
  `MemoryBudgetExceededError` already reports available and total capacity at the only
  moment the number matters.

## [0.1.4] — 2026-07-13

- **`__version__` re-synced to the released version and single-sourced.**
  `edgeproc.__version__` — and with it the `edgeproc version` CLI output and the
  `runtime_version` stamped into every `ResultEnvelope`'s provenance — had been stuck at
  `0.1.1` while the package shipped `0.1.3`. The version now lives only in
  `edgeproc/_version.py`; hatchling reads it at build time (`dynamic = ["version"]`), so the
  installed metadata and `__version__` are one value by construction. A regression test pins
  `importlib.metadata.version("edge-proc") == edgeproc.__version__`.
- **Single-point trust-boundary hardening.** Bundle models now reject non-canonical SHA-256
  values for chunk, file, and manifest digests; direct CAS calls validate digests and resolve
  every object path inside the store root, including symlinked storage directories. Monotonic
  sequences must be non-negative, and reusing an active sequence for different content is
  rejected while exact idempotent replay and legacy signed-pointer bytes remain unchanged.
  `keygen` creates or tightens its output directory to owner-only mode `0700`.

## [0.1.3] — 2026-07-12

Security hardening pass (#11) — **additive runtime safety only**. No persisted or signed
manifest/pointer format changed; `canonical_bytes`, signing, and verification are untouched,
so every already-signed bundle still verifies and materializes unchanged.

- **Local-FS hardening: `O_NOFOLLOW` on key writes + aggregate sync caps.** `keygen` now
  writes `private.key`/`public.key` with `O_NOFOLLOW` (portable via `getattr`), so a symlink
  pre-planted at a key path is refused (ELOOP → fail-closed) instead of redirecting the write
  onto a victim file; materialization was already symlink-safe via the §3.1 containment gate, so
  it is unchanged. `sync_index` gains a fail-closed aggregate ceiling — `max_files` (refused
  before any fetch) and `max_total_bytes` (a running ceiling that aborts before writing the chunk
  that would cross it) — so a hostile or runaway manifest can't enumerate unbounded chunks/files
  to exhaust disk. Defaults are generous (4 GiB / 100k files) and configurable via
  `EdgeProcSettings`; `sync` behavior on a legitimate bundle is unchanged.
- **Signed-pointer identity binding + monotonic sequence (opt-in, backward-compatible).**
  `VersionPointer` gains three optional fields — `bundle_id`, `channel`, `sequence` — folded
  into the signed bytes only when set (`pointer_signing_bytes`), so a pointer that binds none
  of them hashes to the exact legacy `{manifest_hash, version}` preimage and every
  already-signed pointer verifies byte-for-byte. `publish --bind-identity`/`--channel`/
  `--sequence` stamp them; `sync --expected-bundle-id`/`--expected-channel` pin the consumer
  so a validly-signed pointer minted for another bundle/channel (a cross-bundle replay under a
  shared key + transport compromise) is refused before promote, and a pointer whose bound
  `bundle_id` disagrees with its manifest is rejected. A provably-lower `sequence` is refused at
  `promote` alongside the PEP 440 anti-rollback guard, and `is_fresh_sequence` gives a downstream
  a strict-monotonic freshness/anti-replay predicate. All identity/freshness inputs are opt-in;
  the default `publish`/`sync` behavior and the persisted pointer format are unchanged.
- **Trust-boundary path containment (§3.1 trust gate).** New `bundles/containment.py`
  chokepoint refuses traversal (`../`), backslash, and absolute paths. A `FileEntry.path`
  `field_validator` rejects an unsafe path at parse time, and materialization re-checks the
  fully-resolved target still lies inside the output root (catches symlink/zip-slip escapes).
- **Private key written 0600.** `keygen` now writes `private.key` with owner-only
  permissions instead of the umask default (world-readable 0644).
- **Decompression-bomb + oversized-body caps.** CAS decompression streams at most
  `max_decompressed_bytes` (default 64 MiB) rather than trusting the zstd frame's
  content-size header, and the HTTP adapter refuses a response body past `max_fetch_bytes`
  (default 256 MiB) — both fail-closed and configurable via `EdgeProcSettings`.
- **Anti-rollback on promote.** `promote()` refuses a signed pointer whose version is
  provably older (PEP 440) than the active one, so a replayed stale `/latest` cannot
  downgrade a client. Equal/forward versions, first promote, and unparseable versions are
  still allowed — a valid signed bundle is never rejected.
- **FAISS stale-row purge.** Deleting an id then re-inserting it no longer leaves the old
  physical row addressable; search never returns the duplicated/stale-scored entity, and
  `get_stats` counts the superseded row so a rebuild compacts it.
- **CVE lock bumps (#9).** `torch` 2.12.0→2.13.0 (CVE-2025-3000), `cryptography`
  48.0.0→49.0.0 (GHSA-537c-gmf6-5ccf), `pydantic-settings` 2.14.1→2.14.2
  (GHSA-4xgf-cpjx-pc3j) — all in-range lock bumps, no `pyproject` floor changes.

## [0.1.2] — 2026-07-11

Propagation-chain release: re-pins the upstream dependency so downstream consumers can bump
in one hop (`shared-libs-python v0.1.3 → edge-proc v0.1.2 → edge-reco`). No library
code changes.

- **Deps.** `shared-libs-python` git-tag pin bumped v0.1.2 → **v0.1.3** (upstream
  release is gate/CI/docs-only — zero runtime change).

Also ships the house engineering-standard alignment — CI and docs only:

- **CI.** The workflow now literally runs `uv run poe gate`, so the local gate and CI
  can never drift one-sidedly; minimal token permissions; `uv sync --frozen`;
  `astral-sh/setup-uv` full-pinned to v8.3.2 (no floating major tag exists); new
  full-history gitleaks secret-scan job.
- **Security.** Weekly `security-audit.yml` (pip-audit over the exported lock) and
  `dependabot.yml` (weekly, grouped: github-actions + uv ecosystems).
- **Docs.** New `CLAUDE.md` (agent guide: invariants, commands, scarred quality gates);
  the roadmap's WASM entry upgraded to the named "First-party WASM kernel v0" item with
  a gradeable definition of done (README + ROADMAP.md).

## 0.1.1 — 2026-06-19

Public open-source release (MIT). Part of the `edge-reco → edge-proc →
shared-libs-python` stack going public together; live demo at https://edge-reco.com.

- **Clone-and-go onboarding.** `shared-libs-python` is now pulled from public GitHub
  via a git source pinned to a tag (`[tool.uv.sources]`), so `git clone … && uv sync`
  works with no sibling checkout. A commented path-source override remains for local
  co-development.
- **CI simplified.** Dropped the private-sibling checkout + path-patch steps and the
  `PORTFOLIO_PAT` secret — CI now builds exactly as an external cloner does.
- **Docs.** README sharpened to lead with the substrate value proposition (edge compute
  cost, CDN-scale, offline resilience) and cross-link the three-repo stack.

## 0.1.0 — 2026-05-28

First public release: the AI-native local execution substrate as a library + CLI.

- **Core seam.** `EdgeProc` facade, `Runtime`/`Router`/`TelemetrySink` Protocols,
  pure-deterministic `DefaultRouter`, `RuntimeRegistry`, fail-closed
  `no_runtime_accepted` envelope. No LLM in the routing path.
- **LocalVec runtime.** FAISS-backed `EMBED` / `SEARCH` / `RANK` with hybrid BM25 +
  vector RRF fusion. `LocalVecRuntime.from_texts(catalog, encoder=...)` is the
  one-call wiring for the README quickstart.
- **Signed bundle/sync substrate** (`[bundles]` extra). Content-defined chunking
  (GearCDC), content-addressed CAS with zstd compression + atomic promote + GC,
  ed25519-signed `VersionPointer` (the only signed object), fail-closed
  signature/integrity/decompress checks, hardlink-deduped origin layout so
  re-publishing an unchanged catalog touches zero chunks.
- **CLI** (`edgeproc`). `version`, `list-runtimes`, `keygen`, `publish`, `sync`,
  `route` — every fetch path verifies against a pinned trust-root pubkey or
  refuses to run.
- **End-to-end example.** `examples/quickstart.py` + `examples/run_loop.sh`
  exercise keygen → publish → sync → route over a tiny realistic catalog.

The Wasmtime deterministic kernel, Biscuit capability tokens, and Sigstore-keyless
bundles are kept as Protocol seams for future drop-in — not in 0.1.0.
