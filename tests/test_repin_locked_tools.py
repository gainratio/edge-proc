"""Behavior of the Dependabot re-pin tool for lock-derived tool literals."""

from __future__ import annotations

import base64
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

_TOOL_PATH = Path(__file__).resolve().parents[1] / "scripts" / "repin_locked_tools.py"


def _load_tool() -> ModuleType:
    name = "repin_locked_tools_under_test"
    spec = importlib.util.spec_from_file_location(name, _TOOL_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


tool = _load_tool()

TEST_SITE = Path("tests/test_ci_dependency_graph.py")
DAGGER_SITE = Path(".dagger/src/edge_proc/main.py")


def _lock(hatchling: str = "1.33.0", pip_audit: str = "2.11.0") -> str:
    return (
        'version = 1\n\n[[package]]\nname = "hatchling"\n'
        f'version = "{hatchling}"\n\n[[package]]\nname = "pip-audit"\nversion = "{pip_audit}"\n'
    )


def _test_site(hatchling: str, pip_audit: str) -> str:
    return (
        f'    assert build_requirements == ["hatchling=={hatchling}"]\n'
        f'    assert versions["hatchling"] == "{hatchling}"\n'
        f'    assert versions["pip-audit"] == "{pip_audit}"\n'
    )


def _dagger_site(pip_audit: str) -> str:
    return f'    "--from",\n    "pip-audit=={pip_audit}",\n    "pip-audit",\n'


def _repository(tmp_path: Path, lock: str | None = None) -> Path:
    (tmp_path / "uv.lock").write_text(lock or _lock(), encoding="utf-8")
    for site, text in (
        (TEST_SITE, _test_site("1.32.4", "2.10.1")),
        (DAGGER_SITE, _dagger_site("2.10.1")),
    ):
        (tmp_path / site).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / site).write_text(text, encoding="utf-8")
    return tmp_path


def test_should_cover_exactly_the_documented_literal_sites() -> None:
    # Given / When
    sites = {str(path): tools for path, tools in tool.SITES.items()}

    # Then
    assert sites == {
        "tests/test_ci_dependency_graph.py": ("hatchling", "pip-audit"),
        ".dagger/src/edge_proc/main.py": ("pip-audit",),
    }


def test_should_agree_with_the_real_repository_lock() -> None:
    # Given
    root = Path(__file__).resolve().parents[1]

    # When
    planned = tool.plan(root)

    # Then
    assert planned == {}


def test_should_rewrite_every_literal_to_the_locked_version(tmp_path: Path) -> None:
    # Given
    root = _repository(tmp_path)

    # When
    changed = tool.rewrite(root)

    # Then
    assert changed == (TEST_SITE, DAGGER_SITE)
    assert (root / TEST_SITE).read_text(encoding="utf-8") == _test_site("1.33.0", "2.11.0")
    assert (root / DAGGER_SITE).read_text(encoding="utf-8") == _dagger_site("2.11.0")


def test_should_change_nothing_when_literals_match_the_lock(tmp_path: Path) -> None:
    # Given
    root = _repository(tmp_path, _lock("1.32.4", "2.10.1"))

    # When / Then
    assert tool.rewrite(root) == ()


def test_should_refuse_a_site_missing_an_expected_literal(tmp_path: Path) -> None:
    # Given
    root = _repository(tmp_path)
    (root / DAGGER_SITE).write_text("nothing pinned\n", encoding="utf-8")

    # When / Then
    with pytest.raises(tool.RepinError, match="pip-audit"):
        tool.rewrite(root)


def test_should_refuse_a_tool_missing_from_the_lock(tmp_path: Path) -> None:
    # Given
    root = _repository(tmp_path, 'version = 1\n\n[[package]]\nname = "hatchling"\nversion = "1"\n')

    # When / Then
    with pytest.raises(tool.RepinError, match=re.escape("pip-audit is not in uv.lock")):
        tool.rewrite(root)


@pytest.mark.parametrize("version", ["", "1.0\n", '1.0"', "latest", "1.0 ; evil", "../1"])
def test_should_refuse_a_lock_version_that_is_not_a_plain_release(
    tmp_path: Path, version: str
) -> None:
    # Given
    root = _repository(tmp_path, _lock(hatchling=version.replace("\n", "\\n").replace('"', '\\"')))

    # When / Then
    with pytest.raises(tool.RepinError, match="not a plain version"):
        tool.rewrite(root)


def test_should_build_a_head_bound_commit_request(tmp_path: Path) -> None:
    # Given
    root = _repository(tmp_path)

    # When
    request = tool.commit_request(
        root, "gainratio/edge-proc", "dependabot/uv/x", "c" * 40, (TEST_SITE,)
    )

    # Then
    payload = request["variables"]["input"]
    assert payload["expectedHeadOid"] == "c" * 40
    assert payload["branch"]["branchName"] == "dependabot/uv/x"
    addition = payload["fileChanges"]["additions"][0]
    assert addition["path"] == str(TEST_SITE)
    assert base64.b64decode(addition["contents"]) == (root / TEST_SITE).read_bytes()
    assert "createCommitOnBranch" in request["query"]


def test_should_refuse_a_head_that_is_not_a_full_commit_sha(tmp_path: Path) -> None:
    # Given
    root = _repository(tmp_path)

    # When / Then
    with pytest.raises(tool.RepinError, match="commit sha"):
        tool.commit_request(root, "gainratio/edge-proc", "b", "HEAD", (TEST_SITE,))


def test_should_print_changed_sites_from_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    root = _repository(tmp_path)

    # When
    status = tool.main(["rewrite", "--root", str(root)])

    # Then
    assert status == 0
    assert capsys.readouterr().out.split() == [str(TEST_SITE), str(DAGGER_SITE)]


def test_should_print_the_commit_request_json_from_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    root = _repository(tmp_path)
    head = ["--head-sha", "d" * 40, str(TEST_SITE)]

    # When
    status = tool.main(
        ["commit-request", "--root", str(root), "--repository", "r/r", "--branch", "b", *head]
    )

    # Then
    assert status == 0
    assert json.loads(capsys.readouterr().out)["variables"]["input"]["expectedHeadOid"] == "d" * 40


def test_should_exit_one_with_the_refusal_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    root = _repository(tmp_path)
    (root / TEST_SITE).write_text("", encoding="utf-8")

    # When
    status = tool.main(["rewrite", "--root", str(root)])

    # Then
    assert status == 1
    assert "repin refused" in capsys.readouterr().err


HEAD = "e" * 40
REPOSITORY = "gainratio/edge-proc"


class FakeGitHub:
    """Serves one pull request and its head files; records every write."""

    def __init__(self, root: Path, pull: dict[str, object], runs: list[object]) -> None:
        self.root = root
        self.pull = pull
        self.runs = runs
        self.posts: list[tuple[str, object]] = []

    def get_json(self, path: str) -> object:
        if path == f"/repos/{REPOSITORY}/pulls/7":
            return self.pull
        assert path == f"/repos/{REPOSITORY}/actions/workflows/dagger.yml/runs?head_sha={HEAD}"
        return {"workflow_runs": self.runs}

    def get_text(self, path: str) -> str:
        prefix, _, ref = path.partition("?ref=")
        assert ref == HEAD
        site = prefix.removeprefix(f"/repos/{REPOSITORY}/contents/")
        return (self.root / site).read_text(encoding="utf-8")

    def post_json(self, path: str, body: object) -> object:
        self.posts.append((path, body))
        return {"data": {"createCommitOnBranch": {"commit": {"oid": "f" * 40}}}}


def _pull(
    login: str = "dependabot[bot]", repo: str = REPOSITORY, ref: str = "dependabot/uv/x"
) -> dict[str, object]:
    head = {"ref": ref, "sha": HEAD, "repo": {"full_name": repo}}
    return {"user": {"login": login}, "head": head}


def _github(tmp_path: Path, pull: dict[str, object], runs: list[object]) -> FakeGitHub:
    (tmp_path / "head").mkdir()
    return FakeGitHub(_repository(tmp_path / "head"), pull, runs)


def test_should_commit_the_repinned_sites_bound_to_the_head_and_cancel_its_ci(
    tmp_path: Path,
) -> None:
    # Given
    runs = [{"id": 11, "status": "queued"}, {"id": 12, "status": "completed"}]
    github = _github(tmp_path, _pull(), runs)

    # When
    outcome = tool.repin_pull_request(github, REPOSITORY, 7)

    # Then
    (graphql, request), cancel = github.posts
    commit_input = request["variables"]["input"]
    assert graphql == "/graphql"
    assert commit_input["expectedHeadOid"] == HEAD
    assert commit_input["branch"]["branchName"] == "dependabot/uv/x"
    assert [item["path"] for item in commit_input["fileChanges"]["additions"]] == [
        str(TEST_SITE),
        str(DAGGER_SITE),
    ]
    assert cancel == (f"/repos/{REPOSITORY}/actions/runs/11/cancel", {})
    assert outcome == f"re-pinned {TEST_SITE}, {DAGGER_SITE}; cancelled runs 11"


def test_should_write_nothing_when_the_head_literals_match_its_lock(tmp_path: Path) -> None:
    # Given
    github = _github(tmp_path, _pull(), [])
    (github.root / "uv.lock").write_text(_lock("1.32.4", "2.10.1"), encoding="utf-8")

    # When
    outcome = tool.repin_pull_request(github, REPOSITORY, 7)

    # Then
    assert github.posts == []
    assert outcome == "literals already match uv.lock; nothing to commit"


@pytest.mark.parametrize(
    ("pull", "reason"),
    [
        (_pull(login="octocat"), "not opened by dependabot"),
        (_pull(repo="fork/edge-proc"), "not in gainratio/edge-proc"),
        (_pull(ref="dependabot/npm_and_yarn/x"), "not a Dependabot uv branch"),
        (_pull(ref=7), "malformed"),
    ],
)
def test_should_refuse_any_pull_request_outside_the_dependabot_uv_guard(
    tmp_path: Path, pull: dict[str, object], reason: str
) -> None:
    # Given
    github = _github(tmp_path, pull, [])

    # When / Then
    with pytest.raises(tool.RepinError, match=reason):
        tool.repin_pull_request(github, REPOSITORY, 7)
    assert github.posts == []


def test_should_refuse_a_graphql_error_before_cancelling_anything(tmp_path: Path) -> None:
    # Given
    github = _github(tmp_path, _pull(), [{"id": 11, "status": "queued"}])
    github.post_json = lambda path, body: {"errors": [{"message": "head moved"}]}  # type: ignore[method-assign]

    # When / Then
    with pytest.raises(tool.RepinError, match="head moved"):
        tool.repin_pull_request(github, REPOSITORY, 7)


def test_should_refuse_to_run_without_a_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    monkeypatch.delenv("GH_TOKEN", raising=False)

    # When
    status = tool.main(["run", "--repository", REPOSITORY, "--pr", "7"])

    # Then
    assert status == 1
    assert "GH_TOKEN" in capsys.readouterr().err


def test_should_run_against_the_rest_api_with_the_environment_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given
    seen: list[object] = []
    monkeypatch.setenv("GH_TOKEN", "t0ken")

    def fake_repin(github: object, repository: str, number: int) -> str:
        seen.extend((github, repository, number))
        return "done"

    monkeypatch.setattr(tool, "repin_pull_request", fake_repin)

    # When
    status = tool.main(["run", "--repository", REPOSITORY, "--pr", "7"])

    # Then
    assert status == 0
    assert isinstance(seen[0], tool.RestGitHub)
    assert seen[1:] == [REPOSITORY, 7]
    assert capsys.readouterr().out.strip() == "done"


class _Reply:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> _Reply:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_should_send_bearer_requests_with_the_matching_media_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    sent: list[tuple[str, str | None, str | None, bytes | None]] = []
    replies = iter([b'{"a": 1}', b"raw text", b"", b'{"data": {}}'])

    def fake_urlopen(request: object, timeout: int) -> _Reply:
        sent.append(
            (
                request.full_url,  # type: ignore[attr-defined]
                request.get_header("Authorization"),  # type: ignore[attr-defined]
                request.get_header("Accept"),  # type: ignore[attr-defined]
                request.data,  # type: ignore[attr-defined]
            )
        )
        return _Reply(next(replies))

    monkeypatch.setattr(tool.urllib.request, "urlopen", fake_urlopen)
    github = tool.RestGitHub("t0ken", "https://api.test")

    # When
    results = [
        github.get_json("/j"),
        github.get_text("/t"),
        github.post_json("/c", {}),
        github.post_json("/g", {"q": 1}),
    ]

    # Then
    assert results == [{"a": 1}, "raw text", None, {"data": {}}]
    assert [item[0] for item in sent] == [f"https://api.test{p}" for p in ("/j", "/t", "/c", "/g")]
    assert {item[1] for item in sent} == {"Bearer t0ken"}
    assert sent[1][2] == "application/vnd.github.raw+json"
    assert sent[3][3] == b'{"q": 1}'


@pytest.mark.parametrize("base", ["file:///etc", "http://api.github.com"])
def test_should_refuse_an_api_base_that_is_not_https(base: str) -> None:
    with pytest.raises(tool.RepinError, match="https"):
        tool.RestGitHub("t0ken", base)
