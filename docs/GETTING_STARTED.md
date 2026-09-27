# Getting started for developers

This page takes you from nothing to a green local build and your first change. Every command
below was run from a fresh clone on 2026-09-25 (macOS, Apple Silicon); the times are what it
took there.

## Prerequisites

- **Python 3.13 or newer.** `uv` can install it for you: `uv python install 3.13`.
- **[uv](https://docs.astral.sh/uv/)** for the virtual environment and dependencies. Install it
  with `brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`. The lockfile
  (`uv.lock`) is committed, so everyone gets the same versions.
- **About 1 GB of disk** for the virtual environment. FAISS and PyTorch (used by on-device
  search) make up most of it.
- **Optional: [Dagger](https://docs.dagger.io/) 0.21.8 and Docker**, only if you want to run the
  exact job CI runs (see [Run the full check](#run-the-full-check)).

You do not need to clone any other repository. `edgeproc-core`, the one library EdgeProc
builds on, installs from PyPI.

## Clone, install, and run it

```bash
git clone https://github.com/hseshadr/edge-proc.git
cd edge-proc
uv sync --all-extras
```

The clone took 2 s and `uv sync --all-extras` about 3 s with a warm uv cache. A first-ever
install downloads about 75 packages, mostly PyTorch and FAISS, so allow a minute or two on a
normal connection. Success looks like a list of `+ package==version` lines and no error.

Check the command-line tool works:

```bash
uv run edgeproc --help
```

Then run the whole product end to end, on your machine, with no server:

```bash
bash examples/run_loop.sh
```

This took 117 s. It makes a signing key, builds a search index from
[`examples/catalog.json`](../examples/catalog.json), publishes it, syncs it into a fresh
"device" folder, ships a one-file update, runs a search, and shows a refusal. The first run
downloads an 87 MB search model from Hugging Face; later runs reuse it. The last lines
should read:

```text
=== 6. DEVICE — route a SEARCH task using only what sync verified, no network ===
success=True runtime=localvec latency=2349.0ms
  sku-001  0.405
  sku-002  0.478
  sku-010  0.627

=== 7. the guard, shown refusing — same device, same cold cache, no local model ===
    [config.missing] no local embedding model is configured, so EdgeProc will not load 'sentence-transformers/all-MiniLM-L6-v2': fetching it would need the network. Set EDGEPROC_MODEL_PATH to a local model directory (ship the model in the signed bundle and point at what `edgeproc sync --materialize-to` wrote), or set EDGEPROC_ALLOW_MODEL_DOWNLOAD=1 to permit a one-time fetch on a build machine.
  -> refused as expected, and nothing was fetched
```

Only `latency` changes between runs.

## Run the full check

```bash
uv run poe gate
```

This is the check to run before every push. It runs, in order: `ruff` lint, `ruff format
--check`, `mypy --strict`, the `xenon` complexity limit (every function must score Radon
grade A), and `pytest` with at least 90% statement and branch coverage. It took about
3.5 minutes; it prints `Required test coverage of 90% reached` at the end.

`poe gate` is the product-quality portion of the hosted `Dagger` job. Dagger also runs the real
example, benchmark, locked dependency audit, exact snapshot plus full commit-history secret scan,
and workflow validation. A green local product gate alone is not evidence that those controls ran.

To run the exact job CI runs (needs Docker and Dagger 0.21.8):

```bash
rm -f .coverage
dagger call ci --commit-sha=$(git rev-parse HEAD)
```

This took about 10 minutes and ends with exit code 0. It checks the committed code, so commit
first. Known trap: if a `.coverage` file is left over from `poe gate`, it stops early with
`workspace does not match exact commit at .coverage`. That is why the `rm` line is there.

Run the steps one at a time while you work:

```bash
uv run poe lint        # ruff check
uv run poe fmt         # apply formatting (poe fmt-check only checks)
uv run poe typecheck   # mypy --strict
uv run poe complexity  # xenon, Radon grade A
uv run poe test        # pytest with coverage
```

Known trap: running a single test file with plain `uv run pytest tests/some_test.py` passes the
tests but then prints `FAIL Required test coverage of 90% not reached`, because coverage is
measured over the whole package. Add `--no-cov` when you run a subset.

## Map of the code

| Path | What it is |
| --- | --- |
| [`edgeproc/core/`](../edgeproc/core/) | Task and result types, the rule-based router, the runtime registry, the memory budget, and `settings.py` (every setting). |
| [`edgeproc/bundles/`](../edgeproc/bundles/) | Publish and sync: cutting files into pieces (`chunking.py`), the piece store (`cas.py`), signing and key rings (`signing.py`, `keyring.py`), and `sync.py`, which checks everything before a version goes live. |
| [`edgeproc/localvec/`](../edgeproc/localvec/) | On-device search: the embedding model loader (`encoder.py`, `model_source.py`), the FAISS index (`faiss_index.py`), keyword search, and result fusion. |
| [`edgeproc/cli/app.py`](../edgeproc/cli/app.py) | The `edgeproc` command: `keygen`, `keyring`, `publish`, `sync`, `route`, `gc`, `version`, `list-runtimes`. |
| [`edgeproc/errors.py`](../edgeproc/errors.py) | The stable error codes (like `bundle.integrity_failed`), registered with `edgeproc-core`. |
| [`tests/`](../tests/) | One pytest suite. Folders mirror the package; the `test_*_contract*.py` files check that the docs match the code. |
| [`examples/`](../examples/) | `run_loop.sh` (the end-to-end demo) and `quickstart.py` (the build-machine step). |
| [`benchmarks/benchmark.py`](../benchmarks/benchmark.py) | Measures latency and memory on your machine and checks them against budgets. |
| [`docs/`](.) | This page, [ARCHITECTURE.md](ARCHITECTURE.md), [CONFIGURATION.md](CONFIGURATION.md), [QUICKSTART.md](QUICKSTART.md), [OPERATIONS.md](OPERATIONS.md). |
| [`.dagger/`](../.dagger/) | The CI job, written in Python with Dagger. |

## Make your first change

A typical small change is a new setting. Settings are documented in three places, and tests
make sure none is forgotten. Say you want a setting called `demo_flag`.

1. Add the field to `EdgeProcSettings` in
   [`edgeproc/core/settings.py`](../edgeproc/core/settings.py), next to `default_k`:

   ```python
   demo_flag: bool = False
   ```

2. Run the tests that guard settings. They fail and tell you what is missing:

   ```bash
   uv run pytest tests/core/test_settings.py tests/test_release_contract_docs.py -k "setting or env_example" --no-cov
   ```

   ```text
   FAILED tests/core/test_settings.py::test_env_example_documents_every_settings_field
   FAILED tests/test_release_contract_docs.py::test_configuration_reference_documents_every_setting
   FAILED tests/test_release_contract_docs.py::test_configuration_reference_documents_each_setting_with_its_real_env_var
   ```

3. Document it: add `# EDGEPROC_DEMO_FLAG=false` with a comment to
   [`.env.example`](../.env.example), and a row to the table in
   [CONFIGURATION.md](CONFIGURATION.md):

   ```text
   | `demo_flag` | `EDGEPROC_DEMO_FLAG` | `False` | What it does, in one line. |
   ```

4. Add a behaviour test in [`tests/core/test_settings.py`](../tests/core/test_settings.py)
   (copy `test_env_overrides_are_read_with_the_edgeproc_prefix`), run step 2 again until it is
   green, then run `uv run poe gate`.

5. Add a line under `## [Unreleased]` in [CHANGELOG.md](../CHANGELOG.md).

The same pattern holds across the repo: write the failing test first, make it pass with the
smallest change, then run the full check.

## Open a pull request

1. Branch from `main`. Name it after the kind of change: `fix/...`, `feat/...`, `docs/...`,
   `chore/...`.
2. Commit the code and its tests together. Update `CHANGELOG.md` under `[Unreleased]`.
3. Push and open a pull request. The [template](../.github/PULL_REQUEST_TEMPLATE.md) asks what
   changed and why.
4. CI runs one job, `Dagger`, which runs `dagger call ci`: the full check above, plus the real
   example, the benchmark, a dependency audit, a secret scan over the whole Git history, and
   workflow checks. It must be green before merge.

Reviewers look for:

- A test that fails without your change.
- Any check on signatures, fingerprints, or keys still refuses bad input. Never add a path that
  warns and carries on.
- Docs that match the code. The contract tests catch much of this, but not all of it.

Security problems go to [SECURITY.md](../SECURITY.md), not a public issue. More on style and
conduct: [CONTRIBUTING.md](../CONTRIBUTING.md).
