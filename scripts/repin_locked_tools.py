"""Re-pin lock-derived tool literals on Dependabot uv pull requests.

Dagger runs hatchling and pip-audit at exact versions, and the tests pin those
versions as literals so a drift between the lock and the build is caught. Those
literals are copies of what ``uv.lock`` says. When Dependabot bumps a tool in the
lock, this tool copies the locked version into each literal; the tests that
compare literal, lock, and ``pyproject.toml`` stay exactly as strict.

It reads text only. Nothing from the pull request is imported or executed.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Final

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


class RepinError(ValueError):
    """A refusal: the lock or the literal sites are not in a shape this tool trusts."""


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Re-pin lock-derived tool literals.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rewrite").add_argument("--root", type=Path, required=True)
    commit = commands.add_parser("commit-request")
    commit.add_argument("--root", type=Path, required=True)
    for flag in ("--repository", "--branch", "--head-sha"):
        commit.add_argument(flag, required=True)
    commit.add_argument("paths", nargs="+", type=Path)
    return parser


def _rewrite_lines(arguments: argparse.Namespace) -> list[str]:
    return [str(path) for path in rewrite(arguments.root)]


def _commit_lines(arguments: argparse.Namespace) -> list[str]:
    root, repository, branch = arguments.root, arguments.repository, arguments.branch
    request = commit_request(root, repository, branch, arguments.head_sha, arguments.paths)
    return [json.dumps(request)]


COMMANDS: Final = {"rewrite": _rewrite_lines, "commit-request": _commit_lines}


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
