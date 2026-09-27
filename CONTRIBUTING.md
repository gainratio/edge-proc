# Contributing to EdgeProc

Thanks for your interest in EdgeProc — the local-execution substrate that ships signed,
content-addressed data bundles to devices and runs search and ranking there. Contributions
of all sizes are welcome: bug reports, docs fixes, new runtimes, and substrate hardening.

## TL;DR

New here? [Getting started for developers](docs/GETTING_STARTED.md) walks you from a fresh
clone to a green local build and your first change, with the time each step takes.

```bash
git clone https://github.com/hseshadr/edge-proc.git
cd edge-proc
uv sync --all-extras   # core + extras + dev tooling
uv run poe gate        # lint + format-check + typecheck + complexity + test — what CI runs
```

If `uv run poe gate` is green, your change passes the same checks CI does.

## Setup

EdgeProc uses [`uv`](https://docs.astral.sh/uv/) for environment and dependency management
(no `pip install -e` dance, no virtualenv juggling). Install `uv`, then:

```bash
uv sync --all-extras
```

`edgeproc-core` installs from [PyPI](https://pypi.org/project/edgeproc-core/)
(`edgeproc-core>=0.4.3`), so `uv sync` fetches it with no sibling checkout.
Co-developing `edgeproc-core` alongside EdgeProc? Clone it next to this repo and
add the path override commented in `pyproject.toml`.

## Running the gate

The gate is the single source of truth and **mirrors CI exactly**. Run the whole thing:

```bash
uv run poe gate
```

Or run an individual step while iterating:

```bash
uv run poe lint        # ruff check
uv run poe fmt-check   # ruff format --check  (run `uv run poe fmt` to auto-format)
uv run poe typecheck   # mypy --strict
uv run poe complexity  # xenon — Radon Grade A ceiling on every function/module
uv run poe test        # pytest with coverage (--cov-fail-under=90)
```

## Code style & standards

These are enforced by the gate, so there are no surprises at review time:

- **Ruff** for lint and format (line length 100, `py313` target). `uv run poe fmt` fixes
  formatting in place.
- **mypy `--strict`** — fully typed; no untyped defs.
- **Radon Grade A complexity** (`xenon --max-absolute A --max-modules A --max-average A`).
  Keep functions small and flat.
- **≥90% test coverage** (`pytest --cov-fail-under=90`). New behavior ships with tests in
  the same change.
- **Fail-closed by default.** This is a signing/verification substrate: any verification,
  signature, or integrity path must reject on failure, never silently degrade. Don't add a
  fallback that weakens a trust check.

## Proposing changes

1. **Open an issue first** for anything non-trivial — a
   [bug report](.github/ISSUE_TEMPLATE/bug_report.md) or a
   [feature request](.github/ISSUE_TEMPLATE/feature_request.md) — so we can agree on the
   shape before you invest in code.
2. Fork, branch from `main`, and make your change with tests.
3. Run `uv run poe gate` locally and make sure it is green.
4. Open a pull request using the
   [pull request template](.github/PULL_REQUEST_TEMPLATE.md). Describe what changed and why,
   and link the issue it closes.

## Reporting security issues

Please **do not** open a public issue for security vulnerabilities. See
[SECURITY.md](SECURITY.md) for how to report privately — crypto/verification issues are
taken seriously here.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By participating, you
agree to uphold it.

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
