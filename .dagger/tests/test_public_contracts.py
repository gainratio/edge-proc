"""Behavioral contracts for EdgeProc's reusable Dagger composition."""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import tomllib
from pathlib import Path
from typing import Self, cast

import dagger
import pytest

from edge_proc import main as dagger_module
from edge_proc.main import EdgeProc

ROOT = Path(__file__).resolve().parents[2]
CENTRAL_SHA = "9d491851fc5c65ad4a388ed2dd7bb4def4e1f007"
REPOSITORY = "hseshadr/edge-proc"
PROJECT_NAME = "edge-proc"
COMMIT_SHA = "a" * 40
MAX_FUNCTION_LINES = 15


class SharedGuardRejectedError(RuntimeError):
    """A controlled shared-guard failure used by orchestration tests."""


class SyncResult:
    """Record evaluation of one lazy Dagger result."""

    def __init__(self, name: str, events: list[str], error: Exception | None = None) -> None:
        self._name = name
        self._events = events
        self._error = error

    async def sync(self) -> Self:
        if self._error is not None:
            raise self._error
        self._events.append(self._name)
        return self


class RecordingAudit(SyncResult):
    """Record additional commands composed onto the shared audit."""

    def __init__(self, events: list[str], commands: list[list[str]]) -> None:
        super().__init__("audit", events)
        self._commands = commands

    def with_exec(self, command: list[str]) -> Self:
        self._commands.append(command)
        return self


class RecordingFoundation:
    """Record exact source and shared-guard calls."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.bound = cast(dagger.Directory, object())
        self.guard_error: Exception | None = None
        self.green_call: tuple[dagger.Secret, str] | None = None
        self.calls: list[tuple[str, dagger.Directory, str, str]] = []

    def source(
        self, source: dagger.Directory, repository: str, commit_sha: str
    ) -> dagger.Directory:
        self.calls.append(("source", source, repository, commit_sha))
        self.events.append("source")
        return self.bound

    def guard(self, source: dagger.Directory, repository: str, commit_sha: str) -> dagger.Container:
        self.calls.append(("guard", source, repository, commit_sha))
        return cast(dagger.Container, SyncResult("guard", self.events, self.guard_error))

    def green_main(self, github_token: dagger.Secret, repository: str) -> RecordingEvidence:
        self.green_call = github_token, repository
        self.events.append("green-evidence")
        return RecordingEvidence(self.events)


class RecordingEvidence:
    """Return the exact successful Dagger workflow identity."""

    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def workflow_run_id(self) -> str:
        self._events.append("green-run")
        return "6100"

    async def run_attempt(self) -> int:
        self._events.append("green-attempt")
        return 2


class RecordingCandidate:
    """Expose observable candidate evidence and envelope bytes."""

    def __init__(self, name: str, tag: str, events: list[str]) -> None:
        self.name = name
        self.value = tag
        self.events = events
        self.artifact = cast(dagger.Directory, object())
        self.directory = cast(dagger.Directory, RecordingEnvelope(name, events, self.artifact))

    async def tag(self) -> str:
        self.events.append(f"{self.name}-tag")
        return self.value

    def envelope(self) -> dagger.Directory:
        self.events.append(f"{self.name}-envelope")
        return self.directory


class RecordingEnvelope:
    """Expose the authenticated artifact subtree of one Foundation envelope."""

    def __init__(self, name: str, events: list[str], artifact: dagger.Directory) -> None:
        self._name = name
        self._events = events
        self._artifact = artifact

    def directory(self, path: str) -> dagger.Directory:
        assert path == "artifact"
        self._events.append(f"{self._name}-artifact")
        return self._artifact


class RecordingPythonPackage:
    """Record calls across the generated reusable Python-package API."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.created = RecordingCandidate("created", "v0.4.1", events)
        self.verified = RecordingCandidate("verified", "v0.4.1", events)
        self.audit_commands: list[list[str]] = []
        self.audit_result = RecordingAudit(events, self.audit_commands)
        self.audit_call: tuple[dagger.Directory, str, str] | None = None
        self.candidate_call: tuple[object, ...] | None = None
        self.verify_call: tuple[object, ...] | None = None

    def dependency_audit(
        self, source: dagger.Directory, repository: str, commit_sha: str
    ) -> dagger.Container:
        self.audit_call = source, repository, commit_sha
        return cast(dagger.Container, self.audit_result)

    def candidate(self, *arguments: object) -> RecordingCandidate:
        self.candidate_call = arguments
        self.events.append("candidate")
        return self.created

    def verify_candidate(self, **arguments: object) -> RecordingCandidate:
        self.verify_call = tuple(arguments.values())
        self.events.append("verify")
        return self.verified


class RecordingEdgeProc(EdgeProc):
    """Replace the product gate with one observable lazy result."""

    def configure(self, source: dagger.Directory, events: list[str]) -> None:
        self.source = source
        self.events = events
        self.product_source: dagger.Directory | None = None

    def _product_gate(self, source: dagger.Directory) -> dagger.Container:
        self.product_source = source
        return cast(dagger.Container, SyncResult("product", self.events))


class RecordingWorkspace:
    """Record the exact root and exclusions selected by construction."""

    def __init__(self) -> None:
        self.path = ""
        self.excludes: list[str] = []

    def directory(self, path: str, *, exclude: list[str]) -> dagger.Directory:
        self.path = path
        self.excludes = exclude
        return cast(dagger.Directory, object())


def _patch_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[str], RecordingFoundation, RecordingPythonPackage]:
    events: list[str] = []
    foundation = RecordingFoundation(events)
    package = RecordingPythonPackage(events)
    monkeypatch.setattr(dagger_module, "_foundation", lambda: foundation, raising=False)
    monkeypatch.setattr(dagger_module, "_python_package", lambda: package, raising=False)
    return events, foundation, package


def _edge(source: dagger.Directory, events: list[str]) -> RecordingEdgeProc:
    result = RecordingEdgeProc.__new__(RecordingEdgeProc)
    result.configure(source, events)
    return result


def test_should_pin_both_reusable_modules_to_one_exact_central_commit() -> None:
    # Given / When
    config = json.loads((ROOT / "dagger.json").read_text(encoding="utf-8"))

    # Then
    assert config["dependencies"] == [
        {
            "name": "foundation",
            "source": f"github.com/hseshadr/ci/modules/portfolio-foundation@{CENTRAL_SHA}",
            "pin": CENTRAL_SHA,
        },
        {
            "name": "python-package",
            "source": f"github.com/hseshadr/ci/modules/python-package@{CENTRAL_SHA}",
            "pin": CENTRAL_SHA,
        },
    ]
    assert config["include"] == [
        ".dagger/pyproject.toml",
        ".dagger/uv.lock",
        ".dagger/sdk/**",
        ".dagger/src/**",
    ]


def test_should_declare_a_locked_clean_bootstrap_entrypoint() -> None:
    # Given / When
    project = tomllib.loads((ROOT / ".dagger/pyproject.toml").read_text(encoding="utf-8"))

    # Then
    assert project["project"]["entry-points"]["dagger.mod"] == {"main_object": "edge_proc:EdgeProc"}
    assert (ROOT / ".dagger/uv.lock").is_file()


def test_should_construct_from_one_explicit_typed_workspace() -> None:
    # Given
    workspace = RecordingWorkspace()

    # When
    EdgeProc.create(cast(dagger.Workspace, workspace))

    # Then
    assert workspace.path == "/"
    assert {".git", ".venv", ".dagger/sdk", "dist"} <= set(workspace.excludes)
    signature = inspect.signature(EdgeProc.create, eval_str=True)
    assert signature.parameters["workspace"].annotation is dagger.Workspace


def test_should_bind_guard_then_run_product_and_closed_audit_on_one_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    events, foundation, package = _patch_clients(monkeypatch)
    source = cast(dagger.Directory, object())
    edge = _edge(source, events)

    # When
    asyncio.run(edge._run_ci(source, COMMIT_SHA))

    # Then
    assert events == ["source", "guard", "product", "audit"]
    assert foundation.calls == [
        ("source", source, REPOSITORY, COMMIT_SHA),
        ("guard", foundation.bound, REPOSITORY, COMMIT_SHA),
    ]
    assert edge.product_source is foundation.bound
    assert package.audit_call == (foundation.bound, REPOSITORY, COMMIT_SHA)


def test_should_query_osv_for_non_pypi_locked_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    _events, _foundation, package = _patch_clients(monkeypatch)
    edge = _edge(cast(dagger.Directory, object()), [])

    # When
    edge.dependency_audit(COMMIT_SHA)

    # Then
    assert package.audit_commands == [list(dagger_module.OSV_AUDIT_COMMAND)]


def test_should_stop_before_product_or_audit_when_shared_guard_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    events, foundation, package = _patch_clients(monkeypatch)
    foundation.guard_error = SharedGuardRejectedError("shared guard rejected source")
    source = cast(dagger.Directory, object())
    edge = _edge(source, events)

    # When / Then
    with pytest.raises(SharedGuardRejectedError, match="shared guard rejected source"):
        asyncio.run(edge._run_ci(source, COMMIT_SHA))
    assert events == ["source"]
    assert edge.product_source is None
    assert package.audit_call is None


def test_should_create_and_reverify_one_attempt_bound_closed_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    events, foundation, package = _patch_clients(monkeypatch)
    source = cast(dagger.Directory, object())
    token = cast(dagger.Secret, object())
    edge = _edge(source, events)

    # When
    result = asyncio.run(edge.release_candidate("v0.4.1", COMMIT_SHA, token))

    # Then
    identity = (REPOSITORY, COMMIT_SHA, PROJECT_NAME, CENTRAL_SHA, "6100", 2)
    assert events == [
        "source",
        "guard",
        "green-evidence",
        "green-run",
        "green-attempt",
        "candidate",
        "created-tag",
        "created-envelope",
        "verify",
        "verified-tag",
        "verified-envelope",
        "verified-artifact",
    ]
    assert package.candidate_call == (foundation.bound, token, *identity)
    assert package.verify_call == (package.created.directory, *identity)
    assert foundation.green_call == (token, REPOSITORY)
    assert result is package.verified.artifact


def test_should_reject_manual_tag_mismatch_before_candidate_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    events, _foundation, package = _patch_clients(monkeypatch)
    package.created.value = "v9.9.9"
    edge = _edge(cast(dagger.Directory, object()), events)

    # When / Then
    with pytest.raises(ValueError, match="manual tag differs"):
        asyncio.run(edge.release_candidate("v0.4.1", COMMIT_SHA, cast(dagger.Secret, object())))
    assert package.verify_call is None


def test_should_verify_candidate_without_source_or_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    events, foundation, package = _patch_clients(monkeypatch)
    envelope = cast(dagger.Directory, object())
    edge = _edge(cast(dagger.Directory, object()), events)

    # When
    result = asyncio.run(edge.verify_candidate(envelope, COMMIT_SHA, "6100", 2))

    # Then
    identity = (REPOSITORY, COMMIT_SHA, PROJECT_NAME, CENTRAL_SHA, "6100", 2)
    assert package.verify_call == (envelope, *identity)
    assert result is package.verified.directory
    assert foundation.calls == []


def test_should_leave_shared_trust_and_package_build_mechanics_out_of_adapter() -> None:
    # Given / When
    module = inspect.getmodule(EdgeProc)

    # Then
    assert module is not None
    for name in (
        "ACTIONLINT_IMAGE",
        "GITLEAKS_IMAGE",
        "GITLEAKS_SNAPSHOT",
        "GITLEAKS_HISTORY",
    ):
        assert not hasattr(module, name)
    for name in ("_actionlint", "_gitleaks", "_candidate", "_distribution_command"):
        assert not hasattr(EdgeProc, name)


def test_should_keep_every_adapter_function_within_the_python_quality_contract() -> None:
    # Given / When
    source = ROOT / ".dagger/src/edge_proc/main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    functions = (
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )

    # Then
    spans = {node.name: node.end_lineno - node.lineno + 1 for node in functions}
    assert {name: span for name, span in spans.items() if span > MAX_FUNCTION_LINES} == {}
