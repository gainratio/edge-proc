"""Re-pin lock-derived tool literals on Dependabot uv pull requests.

Dagger runs hatchling and pip-audit at exact versions, and the tests pin those
versions as literals so a drift between the lock and the build is caught. Those
literals are copies of what ``uv.lock`` says. When Dependabot bumps a tool in the
lock, this tool copies the locked version into each literal; the tests that
compare literal, lock, and ``pyproject.toml`` stay exactly as strict.

It reads text only. Nothing from the pull request is imported or executed: the
``run`` subcommand fetches the head's lock and literal sites as text through the
GitHub REST API and commits through ``createCommitOnBranch``. The Dagger function
``repin-dependabot`` runs it, so the workflow stays checkout-then-Dagger.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import tempfile
import tomllib
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

SITES: Final[dict[Path, tuple[str, ...]]] = {
    Path("tests/test_ci_dependency_graph.py"): ("hatchling", "pip-audit"),
    Path(".dagger/src/edge_proc/main.py"): ("pip-audit",),
}
PLAIN_VERSION: Final = re.compile(r"[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc|\.post|\.dev)[0-9]+)*")
COMMIT_SHA: Final = re.compile(r"[0-9a-f]{40}")
MUTATION: Final = (
    "mutation($input: CreateCommitOnBranchInput!) "
    "{ createCommitOnBranch(input: $input) { commit { oid url } } }"
)
HEADLINE: Final = "test: re-pin lock-derived tool versions for the Dependabot bump"
BODY: Final = (
    "Automated by .github/workflows/dependabot-repin.yml: each literal now equals\n"
    "the version in this branch's uv.lock. The pin tests are unchanged."
)


API: Final = "https://api.github.com"
UV_BRANCH: Final = "dependabot/uv/"
DEPENDABOT: Final = "dependabot[bot]"
NOTHING: Final = "literals already match uv.lock; nothing to commit"


class RepinError(ValueError):
    """A refusal: the lock or the literal sites are not in a shape this tool trusts."""


class GitHub(Protocol):
    """The three REST/GraphQL verbs the re-pin needs."""

    def get_json(self, path: str) -> object: ...

    def get_text(self, path: str) -> str: ...

    def post_json(self, path: str, body: object) -> object: ...


@dataclass(frozen=True)
class PullRequest:
    """The fields of one pull request the guard and the commit depend on."""

    author: str
    head_repository: str
    branch: str
    head_sha: str


def _patterns(tool: str) -> tuple[re.Pattern[str], ...]:
    name = re.escape(tool)
    return (
        re.compile(rf'(?<="{name}==)[^"]*(?=")'),
        re.compile(rf'(?<=versions\["{name}"\] == ")[^"]*(?=")'),
    )


def locked_versions(root: Path) -> dict[str, str]:
    """Return the locked version of every tool named by a site."""
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    packages = {str(item.get("name")): str(item.get("version")) for item in lock.get("package", [])}
    tools = {tool for names in SITES.values() for tool in names}
    return {tool: _plain(tool, packages) for tool in sorted(tools)}


def _plain(tool: str, packages: dict[str, str]) -> str:
    if tool not in packages:
        raise RepinError(f"{tool} is not in uv.lock")
    if PLAIN_VERSION.fullmatch(packages[tool]) is None:
        raise RepinError(f"{tool} {packages[tool]!r} is not a plain version")
    return packages[tool]


def _pin(text: str, site: Path, tool: str, version: str) -> str:
    total = 0
    for pattern in _patterns(tool):
        text, count = pattern.subn(version, text)
        total += count
    if total == 0:
        raise RepinError(f"{site} has no {tool} literal")
    return text


def plan(root: Path) -> dict[Path, str]:
    """Return the new text of every site whose literals differ from the lock."""
    versions = locked_versions(root)
    changes: dict[Path, str] = {}
    for site, tools in SITES.items():
        before = (root / site).read_text(encoding="utf-8")
        after = before
        for tool in tools:
            after = _pin(after, site, tool, versions[tool])
        if after != before:
            changes[site] = after
    return changes


def rewrite(root: Path) -> tuple[Path, ...]:
    """Write every planned change; return the sites that changed."""
    changes = plan(root)
    for site, text in changes.items():
        (root / site).write_text(text, encoding="utf-8")
    return tuple(changes)


def _addition(root: Path, path: Path) -> dict[str, str]:
    contents = base64.b64encode((root / path).read_bytes()).decode("ascii")
    return {"path": str(path), "contents": contents}


def commit_request(
    root: Path, repository: str, branch: str, head_sha: str, paths: Sequence[Path]
) -> dict[str, object]:
    """Build a createCommitOnBranch request that only applies on top of ``head_sha``."""
    if COMMIT_SHA.fullmatch(head_sha) is None:
        raise RepinError(f"{head_sha!r} is not a full commit sha")
    commit_input = {
        "branch": {"repositoryNameWithOwner": repository, "branchName": branch},
        "expectedHeadOid": head_sha,
        "message": {"headline": HEADLINE, "body": BODY},
        "fileChanges": {"additions": [_addition(root, path) for path in paths]},
    }
    return {"query": MUTATION, "variables": {"input": commit_input}}


class RestGitHub:
    """A token-bound urllib client for api.github.com."""

    def __init__(self, token: str, base: str = API) -> None:
        if not base.startswith("https://"):
            raise RepinError(f"{base!r} is not an https API base")
        self._token = token
        self._base = base

    def _send(self, path: str, accept: str, body: object = None) -> bytes:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Authorization": f"Bearer {self._token}", "Accept": accept}
        url = self._base + path  # the https-only base is checked in __init__
        request = urllib.request.Request(url, data=data, headers=headers)  # noqa: S310
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return bytes(response.read())

    def get_json(self, path: str) -> object:
        return json.loads(self._send(path, "application/vnd.github+json"))

    def get_text(self, path: str) -> str:
        return self._send(path, "application/vnd.github.raw+json").decode("utf-8")

    def post_json(self, path: str, body: object) -> object:
        raw = self._send(path, "application/vnd.github+json", body)
        return json.loads(raw) if raw.strip() else None


def _field(value: object, *keys: str) -> str:
    for key in keys:
        value = value.get(key) if isinstance(value, Mapping) else None
    if not isinstance(value, str):
        raise RepinError(f"pull request field {'.'.join(keys)} is malformed")
    return value


def _pull_request(github: GitHub, repository: str, number: int) -> PullRequest:
    pull = github.get_json(f"/repos/{repository}/pulls/{number}")
    found = PullRequest(
        author=_field(pull, "user", "login"),
        head_repository=_field(pull, "head", "repo", "full_name"),
        branch=_field(pull, "head", "ref"),
        head_sha=_field(pull, "head", "sha"),
    )
    _guard(found, repository)
    return found


def _guard(pull: PullRequest, repository: str) -> None:
    if pull.author != DEPENDABOT:
        raise RepinError(f"pull request is not opened by dependabot: {pull.author}")
    if pull.head_repository != repository:
        raise RepinError(f"head branch is not in {repository}")
    if not pull.branch.startswith(UV_BRANCH):
        raise RepinError(f"{pull.branch} is not a Dependabot uv branch")


def _fetch_head(github: GitHub, repository: str, head_sha: str, root: Path) -> None:
    for site in (Path("uv.lock"), *SITES):
        quoted = urllib.parse.quote(site.as_posix())
        text = github.get_text(f"/repos/{repository}/contents/{quoted}?ref={head_sha}")
        (root / site).parent.mkdir(parents=True, exist_ok=True)
        (root / site).write_text(text, encoding="utf-8")


def _commit(github: GitHub, request: Mapping[str, object]) -> None:
    reply = github.post_json("/graphql", request)
    errors = reply.get("errors") if isinstance(reply, Mapping) else "no reply"
    if errors:
        raise RepinError(f"createCommitOnBranch failed: {errors}")


def _cancel_superseded(github: GitHub, repository: str, head_sha: str) -> list[int]:
    path = f"/repos/{repository}/actions/workflows/dagger.yml/runs?head_sha={head_sha}"
    listing = github.get_json(path)
    runs = listing.get("workflow_runs", []) if isinstance(listing, Mapping) else []
    live = [run["id"] for run in runs if isinstance(run, Mapping) and run["status"] != "completed"]
    for run_id in live:
        github.post_json(f"/repos/{repository}/actions/runs/{run_id}/cancel", {})
    return [int(run_id) for run_id in live]


def repin_pull_request(github: GitHub, repository: str, number: int) -> str:
    """Re-pin one guarded Dependabot uv pull request; return what happened."""
    pull = _pull_request(github, repository, number)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        _fetch_head(github, repository, pull.head_sha, root)
        changed = rewrite(root)
        if not changed:
            return NOTHING
        _commit(github, commit_request(root, repository, pull.branch, pull.head_sha, changed))
    cancelled = _cancel_superseded(github, repository, pull.head_sha)
    sites = ", ".join(str(path) for path in changed)
    return f"re-pinned {sites}; cancelled runs {', '.join(map(str, cancelled)) or 'none'}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Re-pin lock-derived tool literals.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rewrite").add_argument("--root", type=Path, required=True)
    commit = commands.add_parser("commit-request")
    commit.add_argument("--root", type=Path, required=True)
    for flag in ("--repository", "--branch", "--head-sha"):
        commit.add_argument(flag, required=True)
    commit.add_argument("paths", nargs="+", type=Path)
    run = commands.add_parser("run")
    run.add_argument("--repository", required=True)
    run.add_argument("--pr", type=int, required=True)
    return parser


def _rewrite_lines(arguments: argparse.Namespace) -> list[str]:
    return [str(path) for path in rewrite(arguments.root)]


def _commit_lines(arguments: argparse.Namespace) -> list[str]:
    root, repository, branch = arguments.root, arguments.repository, arguments.branch
    request = commit_request(root, repository, branch, arguments.head_sha, arguments.paths)
    return [json.dumps(request)]


def _run_lines(arguments: argparse.Namespace) -> list[str]:
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise RepinError("GH_TOKEN is not set")
    return [repin_pull_request(RestGitHub(token), arguments.repository, arguments.pr)]


COMMANDS: Final = {
    "rewrite": _rewrite_lines,
    "commit-request": _commit_lines,
    "run": _run_lines,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Run one subcommand; print refusals to stderr and exit 1."""
    try:
        arguments = _parser().parse_args(argv)
        lines = COMMANDS[arguments.command](arguments)
    except RepinError as error:
        print(f"repin refused: {error}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
