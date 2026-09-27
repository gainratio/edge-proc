"""Release-contract checks for security, privacy, reliability, and performance docs."""

import re
import sys
from pathlib import Path

import pytest
from packaging.version import Version

ROOT_FOR_IMPORT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_FOR_IMPORT / "benchmarks"))

from benchmark import BUDGETS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "0.5.0"
#: The first edgeproc-core release with Problem Details hardening; pinned as a literal.
CORE_FLOOR = "0.4.3"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_installable_version_names_the_offline_contract_release() -> None:
    """Breaking offline behavior must never hide under the previously published version."""
    from edgeproc import __version__  # noqa: PLC0415

    assert __version__ == RELEASE_VERSION
    assert f"## [{RELEASE_VERSION}]" in _read("CHANGELOG.md")


def test_changelog_keeps_an_unreleased_section_above_the_current_release() -> None:
    """Keep-a-Changelog: `[Unreleased]` is always present and always on top."""
    changelog = _read("CHANGELOG.md")
    released = re.findall(r"^## \[(\d+\.\d+\.\d+)\] — \d{4}-\d{2}-\d{2}$", changelog, re.MULTILINE)
    assert released[0] == RELEASE_VERSION
    assert changelog.index("## [Unreleased]") < changelog.index(f"## [{RELEASE_VERSION}]")


def test_citation_names_the_current_release() -> None:
    assert f"\nversion: {RELEASE_VERSION}\n" in _read("CITATION.cff")


def test_release_notes_tell_single_key_consumers_nothing_changes() -> None:
    """The minor bump adds the keyring; an existing raw `public.key` must keep working."""
    section = _read("CHANGELOG.md").split(f"## [{RELEASE_VERSION}]", maxsplit=1)[1]
    section = " ".join(section.split("\n## [", maxsplit=1)[0].split())
    assert "### Upgrading" in section
    assert "raw 32-byte `public.key`" in section
    assert f"`edgeproc-core>={CORE_FLOOR}`" in section


def test_package_publish_requires_a_fresh_manual_dispatch() -> None:
    """A tag push alone never mutates the package registry."""
    candidate = _read(".github/workflows/release-candidate.yml")
    publisher = _read(".github/workflows/publish.yml")
    assert "workflow_dispatch:" in candidate
    assert "workflow_run:" in publisher
    assert 'tags: ["v*"]' not in candidate + publisher


@pytest.mark.parametrize(
    "heading",
    [
        "## Threat model and trust boundaries",
        "## Privacy and data flow",
        "## Reliability and recovery contract",
        "## Measured performance contract",
    ],
)
def test_operations_contract_documents_every_required_section(heading: str) -> None:
    assert heading in _read("docs/OPERATIONS.md")


def test_readme_links_the_operations_contract() -> None:
    assert "docs/OPERATIONS.md" in _read("README.md")


def test_readme_leads_with_the_real_end_to_end_demo() -> None:
    """A cold reader reaches a runnable result before the long explanation."""
    readme = _read("README.md")
    try_it = readme.index("\n## Try it\n")
    how_it_works = readme.index("\n## How it works\n")

    assert try_it < how_it_works
    assert "bash examples/run_loop.sh" in readme[try_it:how_it_works]


def test_release_copy_stays_true_before_and_after_registry_propagation() -> None:
    """Published metadata must not freeze a temporary registry state into the wheel."""
    readme = _read("README.md")
    workflow = _read(".github/workflows/publish.yml")

    assert f"This README documents EdgeProc {RELEASE_VERSION}" in readme
    assert "PyPI currently serves" not in readme
    assert "edge-proc is not yet on PyPI" not in workflow


def test_corrective_release_marks_the_affected_version_superseded() -> None:
    changelog = _read("CHANGELOG.md")
    release_040 = changelog.split("## [0.4.0]", maxsplit=1)[1].split("## [0.3.1]", maxsplit=1)[0]
    assert "superseded by 0.4.1" in release_040.lower()


def test_roadmap_never_lists_the_live_pypi_distribution_as_future_work() -> None:
    roadmap = _read("ROADMAP.md")
    near_term = roadmap.split("## Near-term", maxsplit=1)[1].split("## Out of scope", maxsplit=1)[0]
    assert f"## Shipped (v{RELEASE_VERSION})" in roadmap
    assert "EdgeProc currently installs from source / git" not in roadmap
    assert "**PyPI distribution**" not in near_term


def test_contributor_guide_names_the_real_registry_dependency_source() -> None:
    guide = _read("CLAUDE.md")
    assert "`edgeproc-core` resolves from PyPI" in guide
    assert "resolves from public GitHub via a tag-pinned git source" not in guide


def test_core_dependency_floor_excludes_superseded_releases() -> None:
    assert f'"edgeproc-core>={CORE_FLOOR}"' in _read("pyproject.toml")
    assert f"`edgeproc-core>={CORE_FLOOR}`" in _read("README.md")


def test_security_policy_supports_only_the_current_release() -> None:
    policy = _read("SECURITY.md")
    assert f"| {RELEASE_VERSION}   | :white_check_mark: |" in policy
    assert f"| < {RELEASE_VERSION} | :x:                |" in policy
    assert "| 0.1.x" not in policy


def test_dagger_installs_core_from_the_locked_pypi_graph() -> None:
    graph = _read(".dagger/src/edge_proc/main.py")
    assert '"uv", "sync", "--frozen", "--all-extras"' in graph
    assert f'"edgeproc-core>={CORE_FLOOR}"' in _read("pyproject.toml")


def test_lockfile_resolves_a_supported_core_release() -> None:
    """The committed graph CI installs must itself clear the declared core floor."""
    lock = _read("uv.lock")
    assert f'{{ name = "edgeproc-core", specifier = ">={CORE_FLOOR}" }}' in lock
    locked = re.search(r'^name = "edgeproc-core"\nversion = "([^"]+)"$', lock, re.MULTILINE)
    assert locked is not None
    assert Version(locked.group(1)) >= Version(CORE_FLOOR)


def test_quickstart_does_not_freeze_a_stale_test_count() -> None:
    quickstart = _read("docs/QUICKSTART.md")
    assert "`uv run poe gate` | about 3.5 min, full test suite" in quickstart
    assert not re.search(r"`uv run poe gate` \|[^\n]*\b\d+ tests\b", quickstart)


def test_corrective_release_names_every_symlink_containment_fix() -> None:
    section = (
        _read("CHANGELOG.md").split("## [0.4.1]", maxsplit=1)[1].split("## [0.4.0]", maxsplit=1)[0]
    )
    release = " ".join(section.split())
    assert "garbage collection refuses symlinked chunk shards and object leaves" in release
    assert "The publisher refuses symlinks under `--src`" in release
    assert "Read-only saved indexes load without writing their snapshot directory" in release
    assert (
        "Publishing verifies and repairs reused chunk objects before advancing `latest`" in release
    )


def test_operations_contract_explains_the_one_commit_snapshot_boundary() -> None:
    operations = _read("docs/OPERATIONS.md")
    assert "generation-addressed" in operations
    assert "one atomic manifest commit" in operations
    assert "previous complete generation" in operations


@pytest.mark.parametrize("document", ["README.md", "docs/QUICKSTART.md"])
def test_runnable_docs_never_edit_the_retired_legacy_snapshot_sidecar(document: str) -> None:
    assert "src/catalog_idx/state.json" not in _read(document)


def test_full_walkthrough_names_the_current_snapshot_directory() -> None:
    """The saved-index walkthrough lives in QUICKSTART; the README links to it."""
    assert "src/catalog_idx/snapshots/" in _read("docs/QUICKSTART.md")
    assert "docs/QUICKSTART.md" in _read("README.md")


@pytest.mark.parametrize("document", ["README.md", "docs/QUICKSTART.md"])
def test_cache_docs_describe_active_as_a_pointer_file(document: str) -> None:
    copy = _read(document)
    assert "`active` pointer file" in copy
    assert "`active` directory" not in copy
    assert "`active/` directory" not in copy


def test_budget_copy_distinguishes_admission_from_native_rss_enforcement() -> None:
    architecture = " ".join(_read("docs/ARCHITECTURE.md").split())
    assert "MemoryManager" in architecture
    assert "not an enforcement boundary for allocations inside FAISS" in architecture
    assert "hard memory limit" in _read("README.md")


def test_operations_contract_links_a_repeatable_benchmark() -> None:
    operations = _read("docs/OPERATIONS.md")
    assert "benchmarks/benchmark.py" in operations
    assert (ROOT / "benchmarks/benchmark.py").is_file()


def test_settings_copy_matches_host_environment_behavior() -> None:
    configuration = _read("docs/CONFIGURATION.md")
    assert "rejects unknown fields" not in configuration + _read("README.md")
    assert "ignores unrelated host variables" in configuration


@pytest.mark.parametrize("doc", ["README.md", "docs/QUICKSTART.md"])
def test_docs_show_the_canonical_code_prefix_on_every_documented_refusal(doc: str) -> None:
    """Documented failure transcripts must match what the CLI really prints.

    The bug this exists to prevent: both transcripts showed a bare message while the CLI
    has always prefixed the canonical code, so a reader comparing their own terminal
    against the docs saw a mismatch on the fail-closed path this project most wants
    trusted. Asserting the bare form is ABSENT is the half that actually catches a
    regression — a correct line trivially contains the bare one as a substring.
    """
    text = _read(doc)
    for code, message in (
        ("config.missing", "no trust root: pass --key"),
        ("bundle.integrity_failed", "sync failed: stored chunk failed to decompress"),
    ):
        assert f"[{code}] {message}" in text
        assert not re.search(rf"^[#\s]*{re.escape(message)}", text, re.MULTILINE)


def _documented_settings() -> set[str]:
    """Setting names in the configuration reference table's first column."""
    rows = re.findall(
        r"^\|\s*`(\w+)`\s*\|\s*`([A-Z_]+)`\s*\|", _read("docs/CONFIGURATION.md"), re.MULTILINE
    )
    return {name for name, _env in rows}


def test_readme_links_the_configuration_reference() -> None:
    assert "docs/CONFIGURATION.md" in _read("README.md")


def test_configuration_reference_documents_every_setting() -> None:
    """Drift lock: the config table must cover the WHOLE settings object, not a subset.

    The bug this exists to prevent: the table documented 11 of 15 fields, so the four
    fail-closed sync ceilings (`max_decompressed_bytes`, `max_fetch_bytes`,
    `max_sync_total_bytes`, `max_sync_files`) were tunable but undiscoverable — a reader
    could only find them by reading the source. Adding a field now fails this test until
    it is documented.
    """
    from edgeproc.core.settings import EdgeProcSettings  # noqa: PLC0415

    assert _documented_settings() == set(EdgeProcSettings.model_fields)


def test_configuration_reference_documents_each_setting_with_its_real_env_var() -> None:
    """A documented env var that the settings object does not bind is worse than absent."""
    from edgeproc.core.settings import EdgeProcSettings  # noqa: PLC0415

    expected = {
        name: str(field.validation_alias or f"EDGEPROC_{name.upper()}")
        for name, field in EdgeProcSettings.model_fields.items()
    }
    rows = re.findall(
        r"^\|\s*`(\w+)`\s*\|\s*`([A-Z_]+)`\s*\|", _read("docs/CONFIGURATION.md"), re.MULTILINE
    )
    assert dict(rows) == expected


# --- performance-claim drift guard ----------------------------------------------------
#
# The bug this exists to prevent: README claimed "55 ms cold-sync p95" while OPERATIONS.md
# said 111.0 ms. The 55 was that run's p50, mislabeled as a p95, and the two documents had
# silently diverged. Every assertion below compares COMMITTED CONSTANTS against COMMITTED
# TEXT — it never runs the benchmark — so it cannot fail on machine variance, only on a
# real documentation defect.

# The metric label in the OPERATIONS.md evidence table -> its key in benchmarks BUDGETS.
_METRIC_BUDGETS = {
    "vector search": "search_p95_ms",
    "cold sync": "cold_p95_ms",
    "warm sync": "warm_p95_ms",
}
# A documented p95 must sit at least this far under the budget the gate enforces. Wide
# enough that normal machine-to-machine spread never trips it; tight enough that a claim
# which has crept up to the gate's edge (where CI would start flaking) is caught first.
_REQUIRED_HEADROOM = 3.0
_EVIDENCE_ROW = re.compile(
    r"^\|\s*([a-z ]+?)\s*\|\s*([\d.]+) ms\s*\|\s*([\d.]+) ms\s*\|\s*([\d.]+) ms\s*\|$",
    re.MULTILINE,
)


def _documented_measurements() -> dict[str, tuple[float, float, float]]:
    """Parse the committed evidence table in OPERATIONS.md -> {metric: (p50, p95, budget)}."""
    return {
        metric: (float(p50), float(p95), float(budget))
        for metric, p50, p95, budget in _EVIDENCE_ROW.findall(_read("docs/OPERATIONS.md"))
    }


def test_operations_documents_every_benchmarked_metric() -> None:
    assert set(_documented_measurements()) == set(_METRIC_BUDGETS)


def test_documented_budgets_match_the_committed_benchmark_budgets() -> None:
    """The budget column is a copy of `benchmarks/benchmark.py` — it must stay a true copy."""
    for metric, (_, _, budget) in _documented_measurements().items():
        assert budget == BUDGETS[_METRIC_BUDGETS[metric]], (
            f"{metric}: doc budget {budget} != committed budget {BUDGETS[_METRIC_BUDGETS[metric]]}"
        )


@pytest.mark.parametrize("metric", sorted(_METRIC_BUDGETS))
def test_documented_p50_is_strictly_below_its_p95(metric: str) -> None:
    """The mislabel guard — this is the exact defect that shipped.

    A p50 copied into the p95 column lands as ``p50 == p95``, so the comparison is STRICT.
    Genuine measurements never tie here: these are floating-point millisecond timings, and
    a tie would mean at least half the samples came back bit-identical.
    """
    p50, p95, _ = _documented_measurements()[metric]
    assert p50 < p95, (
        f"{metric}: documented p50 {p50} ms is not below its p95 {p95} ms — "
        "a p50 was very likely pasted into the p95 column"
    )


@pytest.mark.parametrize("metric", sorted(_METRIC_BUDGETS))
def test_documented_p95_keeps_headroom_under_the_gate_budget(metric: str) -> None:
    p50, p95, budget = _documented_measurements()[metric]
    assert p95 * _REQUIRED_HEADROOM <= budget, (
        f"{metric}: documented p95 {p95} ms is within {_REQUIRED_HEADROOM}x of the "
        f"{budget} ms gate budget — re-measure and re-set the budget before CI flakes"
    )
    assert p50 > 0, f"{metric}: documented p50 must be a real measurement"


def test_readme_defers_percentile_figures_to_the_operations_contract() -> None:
    """ONE source of truth: percentile measurements live in OPERATIONS.md and nowhere else.

    README restating them is exactly how the 55-vs-111 drift happened, so the README must
    link to the contract rather than copy numbers out of it.
    """
    readme = _read("README.md")
    restated = [percentile for percentile in ("p50", "p95") if percentile in readme]
    assert restated == [], (
        f"README restates percentile figures {restated}; link to docs/OPERATIONS.md instead"
    )


@pytest.mark.parametrize("document", ["docs/GETTING_STARTED.md", "docs/QUICKSTART.md"])
def test_local_gate_claim_separates_the_complete_dagger_graph(document: str) -> None:
    """A green product gate is not a substitute for the complete hosted graph."""
    copy = " ".join(_read(document).split())

    assert "`poe gate` is the product-quality portion of the hosted `Dagger` job." in copy
    assert "full commit-history secret scan" in copy
    assert "If it passes locally, CI passes." not in copy


def _registered_cli_commands() -> set[str]:
    """Command names Typer exposes to a real ``edgeproc --help`` consumer."""
    from edgeproc.cli import app  # noqa: PLC0415

    commands = {
        command.name or command.callback.__name__.replace("_", "-")
        for command in app.registered_commands
        if command.callback is not None
    }
    # A sub-app (`edgeproc keyring ...`) is a top-level command to a consumer, too.
    groups = {group.name for group in app.registered_groups if group.name is not None}
    return commands | groups


def _documented_cli_commands() -> set[str]:
    """Command names in ARCHITECTURE's machine-checkable CLI inventory."""
    architecture = _read("docs/ARCHITECTURE.md")
    inventory = architecture.split("Typer entrypoints:", maxsplit=1)[1].split("|", maxsplit=1)[0]
    return set(re.findall(r"`([a-z-]+)`", inventory))


def test_architecture_inventory_names_every_shipped_cli_command() -> None:
    """Adding or documenting a command on only one side is release-contract drift."""
    assert _documented_cli_commands() == _registered_cli_commands()


@pytest.mark.parametrize("document", ["docs/OPERATIONS.md", "docs/ARCHITECTURE.md"])
def test_persistence_docs_name_the_stable_read_only_lock_free_path(document: str) -> None:
    """Immutable consumers should not be told that every load takes the writer lock."""
    assert "Stable read-only loads do not take that lock" in " ".join(_read(document).split())


def test_current_changelog_names_the_stable_read_only_lock_free_path() -> None:
    """The corrective release note records the shipped immutable-load behavior."""
    section = _read("CHANGELOG.md").split("## [0.4.1]", maxsplit=1)[1]
    current = " ".join(section.split("## [0.4.0]", maxsplit=1)[0].split())

    assert "stable read-only loads do not take the snapshot lock" in current.lower()
    assert "Load, save, migration, and snapshot cleanup share one bounded" not in current


def test_release_runbook_says_dagger_proves_exact_manual_candidate() -> None:
    """A manual request on an arbitrary commit cannot inherit another CI result."""
    release = " ".join(
        _read("docs/OPERATIONS.md").split("## Release evidence", maxsplit=1)[1].split()
    )

    assert "manual `workflow_dispatch`" in release
    assert "Dagger runs all five checks" in release
    assert "exact current `main` commit" in release
    assert "green hosted `Dagger` check" in release
    assert "full Git history" in release


def test_release_runbook_keeps_build_code_outside_the_oidc_job() -> None:
    """Trusted-publish credentials must not be exposed to a package build backend."""
    release = " ".join(
        _read("docs/OPERATIONS.md").split("## Release evidence", maxsplit=1)[1].split()
    )

    assert "Dagger builds and validates in an unprivileged job" in release
    assert (
        "OIDC-bearing job only invokes pinned artifact download and official PyPI publish"
        in release
    )


def test_operations_contract_has_an_honest_key_rotation_runbook() -> None:
    """The runbook documents the SHIPPED keyring — and its limits — never more than that."""
    operations = _read("docs/OPERATIONS.md")
    assert "## Key rotation and compromised-key runbook" in operations
    runbook = operations.split("## Key rotation and compromised-key runbook", 1)[1]
    runbook = runbook.split("\n## ", 1)[0]
    for fact in (
        "### Upgrade order: consumers first, then stamping",
        "### Planned rotation (overlap window)",
        "### Compromised signing key",
        "### Expiry and the re-sign cadence",
        "edgeproc keyring revoke",
        "strictly greater `sequence`",
        "off by default",
        # The honest limits: revocation is local config; expiry does not answer compromise.
        "there is no remotely fetched",
        "Expiry bounds a freeze, not a compromise.",
    ):
        assert fact in runbook, fact
    assert "docs/OPERATIONS.md#key-rotation-and-compromised-key-runbook" in _read("SECURITY.md")


def test_roadmap_lists_the_keyring_as_shipped_and_nothing_it_did_not_ship() -> None:
    roadmap = _read("ROADMAP.md")
    shipped = roadmap.split(f"## Shipped (v{RELEASE_VERSION})", 1)[1].split("\n## ", 1)[0]
    assert "Trust-root keyring" in shipped
    assert "no remotely fetched, signed revocation" in shipped
    near_term = roadmap.split("## Near-term", 1)[1].split("\n## ", 1)[0]
    assert "keyring" not in near_term.lower()
