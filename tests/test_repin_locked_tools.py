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
        root, "hseshadr/edge-proc", "dependabot/uv/x", "c" * 40, (TEST_SITE,)
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
        tool.commit_request(root, "hseshadr/edge-proc", "b", "HEAD", (TEST_SITE,))


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
