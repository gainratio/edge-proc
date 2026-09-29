"""EdgeProc's reusable quality, security, and Python release graph."""

from __future__ import annotations

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
REPOSITORY: Final = "hseshadr/edge-proc"
PROJECT_NAME: Final = "edge-proc"
CENTRAL_MODULE_SHA: Final = "9d491851fc5c65ad4a388ed2dd7bb4def4e1f007"
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
    async def security(self, commit_sha: str) -> str:
        """Run the shared exact-source, workflow, and history guard."""
        await self._verified_source(self.source, commit_sha)
        return "EdgeProc shared Dagger security gate passed"

    @function
    def dependency_audit(self, commit_sha: str) -> dagger.Container:
        """Audit the locked graph through the shared Python-package Lego."""
        return self._dependency_audit(self.source, commit_sha)

    @function
    @check
    async def ci(self, commit_sha: str) -> str:
        """Run the canonical exact-source gate sequentially."""
        await self._run_ci(self.source, commit_sha)
        return "EdgeProc canonical Dagger gate passed"

    # fmt: off
    @function(cache="never")  # type: ignore[call-overload,untyped-decorator]  # SDK stub gap
    async def release_candidate(
        self, tag: str, commit_sha: str, github_token: dagger.Secret,
    ) -> dagger.Directory:
        """Create and reverify one exact attempt-bound Foundation envelope."""
        bound = await self._verified_source(self.source, commit_sha)
        workflow_run_id, run_attempt = await self._green_workflow_identity(github_token)
        candidate = self._create_candidate(
            bound, commit_sha, workflow_run_id, run_attempt, github_token
        )
        await self._require_candidate_tag(candidate, tag)
        return await self._reverified_artifact(
            candidate, tag, commit_sha, workflow_run_id, run_attempt
        )
    # fmt: on

    @function
    async def verify_candidate(
        self,
        envelope: dagger.Directory,
        commit_sha: str,
        workflow_run_id: str,
        run_attempt: int,
    ) -> dagger.Directory:
        """Revalidate a closed candidate without source or credentials."""
        verified = self._candidate_verifier(envelope, commit_sha, workflow_run_id, run_attempt)
        await verified.tag()
        return verified.envelope()

    async def _run_ci(self, source: dagger.Directory, commit_sha: str) -> None:
        bound = await self._verified_source(source, commit_sha)
        await self._product_gate(bound).sync()
        await self._dependency_audit(bound, commit_sha).sync()

    @staticmethod
    def _dependency_audit(source: dagger.Directory, commit_sha: str) -> dagger.Container:
        shared = _python_package().dependency_audit(source, REPOSITORY, commit_sha)
        return shared.with_exec(list(OSV_AUDIT_COMMAND))

    async def _verified_source(self, source: dagger.Directory, commit_sha: str) -> dagger.Directory:
        foundation = _foundation()
        bound = foundation.source(source, REPOSITORY, commit_sha)
        await foundation.guard(bound, REPOSITORY, commit_sha).sync()
        return bound

    @staticmethod
    async def _green_workflow_identity(github_token: dagger.Secret) -> tuple[str, int]:
        evidence = _foundation().green_main(github_token, REPOSITORY)
        return await evidence.workflow_run_id(), await evidence.run_attempt()

    def _product_gate(self, source: dagger.Directory) -> dagger.Container:
        result = self._quality(source).with_exec(["bash", "examples/run_loop.sh"])
        return result.with_exec(["uv", "run", "python", "benchmarks/benchmark.py"])

    # fmt: off
    def _create_candidate(
        self,
        source: dagger.Directory,
        commit_sha: str,
        workflow_run_id: str,
        run_attempt: int,
        github_token: dagger.Secret,
    ) -> PythonPackageCandidate:
        return _python_package().candidate(
            source, github_token, REPOSITORY, commit_sha, PROJECT_NAME,
            CENTRAL_MODULE_SHA, workflow_run_id, run_attempt,
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
        commit_sha: str,
        workflow_run_id: str,
        run_attempt: int,
    ) -> dagger.Directory:
        verified = self._candidate_verifier(
            candidate.envelope(), commit_sha, workflow_run_id, run_attempt
        )
        await self._require_candidate_tag(verified, tag)
        return verified.envelope().directory("artifact")

    @staticmethod
    def _candidate_verifier(
        envelope: dagger.Directory,
        commit_sha: str,
        workflow_run_id: str,
        run_attempt: int,
    ) -> PythonPackageCandidate:
        return _python_package().verify_candidate(
            envelope=envelope,
            repository=REPOSITORY,
            commit_sha=commit_sha,
            project_name=PROJECT_NAME,
            central_module_sha=CENTRAL_MODULE_SHA,
            workflow_run_id=workflow_run_id,
            run_attempt=run_attempt,
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
