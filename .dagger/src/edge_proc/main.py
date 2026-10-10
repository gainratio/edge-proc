"""EdgeProc's reusable quality, security, and Python release graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Self

import dagger
from dagger import check, dag, field, function, object_type
from dagger.client.gen import Foundation, PythonPackage, PythonPackageCandidate

PYTHON_IMAGE: Final = (
    "python:3.13.14-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6"
)
UV_IMAGE: Final = (
    "ghcr.io/astral-sh/uv:0.11.32@sha256:"
    "df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c"
)
#: The only identities a run may claim: the canonical gainratio owner first, then the
#: pre-transfer identity until the org move finishes. There is deliberately no default:
#: every gate must be handed the run's own `github.repository`.
ALLOWED_REPOSITORIES: Final = ("gainratio/edge-proc", "hseshadr/edge-proc")
PROJECT_NAME: Final = "edge-proc"
CENTRAL_MODULE_SHA: Final = "528eaec76121b75810c58bab610d9f2064b95227"
SOURCE_EXCLUDES: Final = [
    ".git",
    ".venv",
    ".dagger/.venv",
    ".dagger/sdk",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "**/__pycache__",
    "dist",
]
OSV_AUDIT_COMMAND: Final = (
    "uv",
    "tool",
    "run",
    "--from",
    "pip-audit==2.10.1",
    "pip-audit",
    "-r",
    "/work/requirements.txt",
    "--disable-pip",
    "--no-deps",
    "--vulnerability-service",
    "osv",
    "--strict",
    "--progress-spinner",
    "off",
    "--verbose",
)


def _allowed_repository(repository: str) -> str:
    """Return the run's repository only when it exactly matches one allowed identity."""
    if repository not in ALLOWED_REPOSITORIES:
        raise ValueError(f"{repository!r} is not an allowed edge-proc repository")
    return repository


@dataclass(frozen=True)
class _Lineage:
    """One release candidate's exact repository, commit, and green-run identity."""

    repository: str
    commit_sha: str
    workflow_run_id: str
    run_attempt: int


def _foundation() -> Foundation:
    """Return the exact-SHA generated Foundation dependency."""
    return dag.foundation()


def _python_package() -> PythonPackage:
    """Return the exact-SHA generated Python-package dependency."""
    return dag.python_package()


@object_type
class EdgeProc:
    """Run the same typed EdgeProc graph locally and on GitHub."""

    source: dagger.Directory = field()

    @classmethod
    def create(cls, workspace: dagger.Workspace) -> Self:
        """Construct the graph from one explicit typed workspace snapshot."""
        instance = cls.__new__(cls)
        instance.source = workspace.directory("/", exclude=SOURCE_EXCLUDES)
        return instance

    @function
    def quality(self) -> dagger.Container:
        """Run EdgeProc's complete repository-owned product gate."""
        return self._quality(self.source)

    @function
    async def security(self, commit_sha: str, repository: str) -> str:
        """Run the shared exact-source, workflow, and history guard."""
        await self._verified_source(self.source, _allowed_repository(repository), commit_sha)
        return "EdgeProc shared Dagger security gate passed"

    @function
    def dependency_audit(self, commit_sha: str, repository: str) -> dagger.Container:
        """Audit the locked graph through the shared Python-package Lego."""
        return self._dependency_audit(self.source, _allowed_repository(repository), commit_sha)

    @function
    @check
    async def ci(self, commit_sha: str, repository: str) -> str:
        """Run the canonical exact-source gate sequentially."""
        await self._run_ci(self.source, commit_sha, _allowed_repository(repository))
        return "EdgeProc canonical Dagger gate passed"

    # fmt: off
    @function(cache="never")  # type: ignore[call-overload,untyped-decorator]  # SDK stub gap
    async def release_candidate(
        self, tag: str, commit_sha: str, github_token: dagger.Secret,
        repository: str,
    ) -> dagger.Directory:
        """Create and reverify one exact attempt-bound Foundation envelope."""
        repository = _allowed_repository(repository)
        bound = await self._verified_source(self.source, repository, commit_sha)
        run_id, attempt = await self._green_workflow_identity(github_token, repository)
        lineage = _Lineage(repository, commit_sha, run_id, attempt)
        candidate = self._create_candidate(bound, lineage, github_token)
        await self._require_candidate_tag(candidate, tag)
        return await self._reverified_artifact(candidate, tag, lineage)
    # fmt: on

    @function(cache="never")  # type: ignore[call-overload,untyped-decorator]  # SDK stub gap
    async def repin_dependabot(
        self, github_token: dagger.Secret, pr_number: int, repository: str
    ) -> str:
        """Copy uv.lock tool versions into the literals of one Dependabot uv PR."""
        repository = _allowed_repository(repository)
        tool = self.source.file("scripts/repin_locked_tools.py")
        command = ["python", "/opt/repin/repin_locked_tools.py", "run"]
        command += ["--repository", repository, "--pr", str(pr_number)]
        container = dag.container().from_(PYTHON_IMAGE)
        container = container.with_file("/opt/repin/repin_locked_tools.py", tool)
        container = container.with_secret_variable("GH_TOKEN", github_token)
        return await container.with_user("65532:65532").with_exec(command).stdout()

    @function
    async def verify_candidate(
        self,
        envelope: dagger.Directory,
        commit_sha: str,
        workflow_run_id: str,
        run_attempt: int,
        repository: str,
    ) -> dagger.Directory:
        """Revalidate a closed candidate without source or credentials."""
        lineage = _Lineage(
            _allowed_repository(repository), commit_sha, workflow_run_id, run_attempt
        )
        verified = self._candidate_verifier(envelope, lineage)
        await verified.tag()
        return verified.envelope()

    async def _run_ci(self, source: dagger.Directory, commit_sha: str, repository: str) -> None:
        bound = await self._verified_source(source, repository, commit_sha)
        await self._product_gate(bound).sync()
        await self._dependency_audit(bound, repository, commit_sha).sync()

    @staticmethod
    def _dependency_audit(
        source: dagger.Directory, repository: str, commit_sha: str
    ) -> dagger.Container:
        shared = _python_package().dependency_audit(source, repository, commit_sha)
        return shared.with_exec(list(OSV_AUDIT_COMMAND))

    async def _verified_source(
        self, source: dagger.Directory, repository: str, commit_sha: str
    ) -> dagger.Directory:
        foundation = _foundation()
        bound = foundation.source(source, repository, commit_sha)
        await foundation.guard(bound, repository, commit_sha).sync()
        return bound

    @staticmethod
    async def _green_workflow_identity(
        github_token: dagger.Secret, repository: str
    ) -> tuple[str, int]:
        evidence = _foundation().green_main(github_token, repository)
        return await evidence.workflow_run_id(), await evidence.run_attempt()

    def _product_gate(self, source: dagger.Directory) -> dagger.Container:
        result = self._quality(source).with_exec(["bash", "examples/run_loop.sh"])
        return result.with_exec(["uv", "run", "python", "benchmarks/benchmark.py"])

    # fmt: off
    def _create_candidate(
        self, source: dagger.Directory, lineage: _Lineage, github_token: dagger.Secret,
    ) -> PythonPackageCandidate:
        return _python_package().candidate(
            source, github_token, lineage.repository, lineage.commit_sha, PROJECT_NAME,
            CENTRAL_MODULE_SHA, lineage.workflow_run_id, lineage.run_attempt,
        )
    # fmt: on

    @staticmethod
    async def _require_candidate_tag(candidate: PythonPackageCandidate, expected: str) -> None:
        if await candidate.tag() != expected:
            raise ValueError("manual tag differs from the metadata-derived candidate tag")

    async def _reverified_artifact(
        self,
        candidate: PythonPackageCandidate,
        tag: str,
        lineage: _Lineage,
    ) -> dagger.Directory:
        verified = self._candidate_verifier(candidate.envelope(), lineage)
        await self._require_candidate_tag(verified, tag)
        return verified.envelope().directory("artifact")

    @staticmethod
    def _candidate_verifier(
        envelope: dagger.Directory, lineage: _Lineage
    ) -> PythonPackageCandidate:
        return _python_package().verify_candidate(
            envelope=envelope,
            repository=lineage.repository,
            commit_sha=lineage.commit_sha,
            project_name=PROJECT_NAME,
            central_module_sha=CENTRAL_MODULE_SHA,
            workflow_run_id=lineage.workflow_run_id,
            run_attempt=lineage.run_attempt,
        )

    def _python(self, source: dagger.Directory) -> dagger.Container:
        base = self._python_toolchain().with_directory("/src", source, owner="65532:65532")
        base = base.with_workdir("/src").with_env_variable("UV_PROJECT_ENVIRONMENT", "/opt/venv")
        base = base.with_env_variable("UV_CACHE_DIR", "/opt/uv-cache")
        base = base.with_env_variable("UV_LINK_MODE", "copy")
        base = base.with_env_variable("HOME", "/opt/home")
        base = base.with_env_variable("XDG_CACHE_HOME", "/opt/model-cache")
        base = base.with_env_variable("HF_HOME", "/opt/model-cache/huggingface")
        base = base.with_env_variable("TMPDIR", "/opt/tmp")
        return self._unprivileged_python(base)

    @staticmethod
    def _unprivileged_python(base: dagger.Container) -> dagger.Container:
        paths = ["/opt/venv", "/opt/home", "/opt/model-cache", "/opt/tmp"]
        result = base.with_mounted_cache(
            "/opt/uv-cache", dag.cache_volume("edge-proc-uv-nonroot"), owner="65532:65532"
        )
        result = result.with_exec(["mkdir", "-p", *paths])
        result = result.with_exec(["chown", "-R", "65532:65532", *paths])
        return result.with_user("65532:65532").with_exec(["uv", "sync", "--frozen", "--all-extras"])

    def _quality(self, source: dagger.Directory) -> dagger.Container:
        return self._python(source).with_exec(["uv", "run", "poe", "gate"])

    @staticmethod
    def _python_toolchain() -> dagger.Container:
        uv = dag.container().from_(UV_IMAGE).file("/uv")
        return dag.container().from_(PYTHON_IMAGE).with_file("/usr/local/bin/uv", uv)
