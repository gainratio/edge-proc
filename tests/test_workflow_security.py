"""Executable contracts for thin Dagger ingress and source-free PyPI OIDC."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
PINNED = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$")
DAGGER_ACTION = "dagger/dagger-for-github"
CHECKOUT_ACTION = "actions/checkout"
UPLOAD_ACTION = "actions/upload-artifact"
DOWNLOAD_ACTION = "actions/download-artifact"
PUBLISH_ACTION = "pypa/gh-action-pypi-publish"


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {"on" if key is True else str(key): item for key, item in value.items()}


def _workflow(name: str) -> dict[str, object]:
    path = WORKFLOWS / name
    return _mapping(yaml.safe_load(path.read_text(encoding="utf-8")))


def _job(document: Mapping[str, object], name: str) -> dict[str, object]:
    return _mapping(_mapping(document.get("jobs")).get(name))


def _steps(job: Mapping[str, object]) -> list[dict[str, object]]:
    steps = job.get("steps")
    return [_mapping(step) for step in steps] if isinstance(steps, list) else []


def _action(step: Mapping[str, object]) -> str:
    uses = step.get("uses")
    return str(uses).split("@")[0] if isinstance(uses, str) else ""


def _workflow_files(directory: Path) -> list[Path]:
    return sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")])


def _external_actions(directory: Path) -> list[str]:
    uses = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", re.MULTILINE)
    return [
        action
        for path in _workflow_files(directory)
        for action in uses.findall(path.read_text(encoding="utf-8"))
        if not action.startswith("./")
    ]


def _assert_thin_dagger(job: Mapping[str, object], args: str) -> None:
    steps = _steps(job)
    assert [_action(step) for step in steps] == [CHECKOUT_ACTION, DAGGER_ACTION]
    assert all(PINNED.fullmatch(str(step.get("uses"))) for step in steps)
    checkout = _mapping(steps[0].get("with"))
    invocation = _mapping(steps[1].get("with"))
    assert checkout.get("fetch-depth") == 0
    assert checkout.get("persist-credentials") is False
    assert invocation == {"version": "0.21.8", "verb": "call", "args": args}


def test_should_pin_every_external_action_in_yml_and_yaml() -> None:
    actions = _external_actions(WORKFLOWS)
    assert actions
    assert [action for action in actions if PINNED.fullmatch(action) is None] == []


def test_should_fail_pin_audit_for_a_yaml_bypass(tmp_path: Path) -> None:
    (tmp_path / "bypass.yaml").write_text(
        "jobs:\n  bypass:\n    steps:\n      - uses: actions/checkout@v4\n",
        encoding="utf-8",
    )
    assert _external_actions(tmp_path) == ["actions/checkout@v4"]


def test_should_route_pull_request_and_main_ci_only_through_dagger() -> None:
    document = _workflow("dagger.yml")
    job = _job(document, "dagger")
    _assert_thin_dagger(job, "ci --commit-sha=${{ github.sha }}")
    assert job.get("name") == "Dagger"


def test_should_route_scheduled_dependency_audit_with_exact_caller_identity() -> None:
    document = _workflow("security-audit.yml")
    _assert_thin_dagger(
        _job(document, "dependency-audit"),
        "dependency-audit --commit-sha=${{ github.sha }}",
    )


#: The release invocation, as `dagger/dagger-for-github` args. The action pastes args into
#: its bash script, so every value is a double-quoted environment variable: bash expands
#: it as one literal word and never parses it as code. No `${{ }}` expression appears.
RELEASE_ARGS = (
    'release-candidate --tag="$TAG" --commit-sha="$GITHUB_SHA" '
    "--github-token=env:GITHUB_TOKEN export --path=release"
)

#: Dispatch tags an attacker could type; each must reach Dagger as one inert argument.
HOSTILE_TAGS = (
    "v0.5.0",
    "",
    "v0.5.0 --commit-sha=0",
    "v0.5.0;touch pwned",
    "$(touch pwned)",
    "`touch pwned`",
    "v0.5.0\ntouch pwned",
    'v0.5.0" ; touch pwned ; "',
)

#: Expression contexts an outside actor can shape: dispatch inputs, event payloads
#: (issue titles, PR bodies, branch names), and the PR head ref.
UNTRUSTED_EXPRESSION = re.compile(r"\$\{\{[^}]*\b(?:inputs\.|github\.event\.|github\.head_ref)")


def _shell_sinks(step: Mapping[str, object]) -> list[str]:
    """Text GitHub pastes into a shell: ``run`` bodies and every Dagger action input.

    `dagger/dagger-for-github` is a composite action that interpolates its inputs
    (``args`` included) into its own bash steps unquoted, so each of its ``with``
    values is shell text too.
    """
    sinks = [str(step["run"])] if "run" in step else []
    if _action(step) == DAGGER_ACTION:
        sinks.extend(str(value) for value in _mapping(step.get("with")).values())
    return sinks


def _injectable_sinks(directory: Path) -> list[str]:
    return [
        f"{path.name}: {sink}"
        for path in _workflow_files(directory)
        for job in _mapping(_mapping(yaml.safe_load(path.read_text())).get("jobs")).values()
        for step in _steps(_mapping(job))
        for sink in _shell_sinks(step)
        if UNTRUSTED_EXPRESSION.search(sink)
    ]


def _expand_action_args(args: str, tag: str, cwd: Path) -> list[str]:
    """Expand args exactly as dagger-for-github's final bash step does, but print them."""
    bash = shutil.which("bash")
    assert bash is not None
    env = {"TAG": tag, "GITHUB_SHA": "a" * 40, "PATH": "/usr/bin:/bin"}
    result = subprocess.run(  # noqa: S603
        [bash, "-c", f"printf '%s\\0' {args}"], env=env, cwd=cwd, capture_output=True, check=True
    )
    return result.stdout.decode().split("\0")[:-1]


def _release_steps() -> list[dict[str, object]]:
    return _steps(_job(_workflow("release-candidate.yml"), "candidate"))


def test_should_make_release_manual_and_dagger_proven() -> None:
    document = _workflow("release-candidate.yml")
    triggers = _mapping(document.get("on"))
    steps = _release_steps()
    assert set(triggers) == {"workflow_dispatch"}
    assert [_action(step) for step in steps] == [CHECKOUT_ACTION, DAGGER_ACTION, UPLOAD_ACTION]
    assert all(PINNED.fullmatch(str(step.get("uses"))) for step in steps)
    checkout = _mapping(steps[0].get("with"))
    assert "ref" not in checkout
    assert checkout.get("persist-credentials") is False
    upload = _mapping(steps[2].get("with"))
    assert upload.get("name") == "edge-proc-${{ github.sha }}"


def test_should_run_no_shell_step_in_the_release_candidate() -> None:
    # Given the release workflow (central fleet policy: shell-step, candidate-order)
    # Then no step runs repository-authored shell; Dagger is the only executor
    assert [step for step in _release_steps() if "run" in step] == []


def test_should_call_the_release_graph_with_only_quoted_environment_values() -> None:
    # Given the Dagger step of the release workflow
    release = _release_steps()[1]
    # Then the tag arrives only through the environment and the args hold no expression
    assert _mapping(release.get("env")) == {
        "TAG": "${{ inputs.tag }}",
        "GITHUB_TOKEN": "${{ github.token }}",
    }
    invocation = _mapping(release.get("with"))
    assert invocation == {"version": "0.21.8", "verb": "call", "args": RELEASE_ARGS}
    assert "--workflow-run-id" not in RELEASE_ARGS
    assert "--run-attempt" not in RELEASE_ARGS


@pytest.mark.parametrize("tag", HOSTILE_TAGS)
def test_should_pass_any_dispatched_tag_to_dagger_as_one_inert_argument(
    tag: str, tmp_path: Path
) -> None:
    # Given the release args expanded the way the pinned action's bash expands them
    args = str(_mapping(_release_steps()[1].get("with")).get("args"))
    # When a hostile tag is dispatched
    words = _expand_action_args(args, tag, tmp_path)
    # Then Dagger receives the tag verbatim as one argument and no command ran
    assert words[:2] == ["release-candidate", f"--tag={tag}"]
    assert words[2] == "--commit-sha=" + "a" * 40
    assert list(tmp_path.iterdir()) == []


def test_should_keep_untrusted_expressions_out_of_every_workflow_shell() -> None:
    # Given every workflow in the repository
    # Then no dispatch input or event payload is pasted into shell text
    assert _injectable_sinks(WORKFLOWS) == []


def test_should_flag_an_input_pasted_into_a_run_or_dagger_args(tmp_path: Path) -> None:
    # Given the pre-fix release shape and a raw run-step interpolation
    (tmp_path / "bad.yml").write_text(
        "jobs:\n"
        "  bad:\n"
        "    steps:\n"
        "      - run: echo ${{ github.event.pull_request.title }}\n"
        "      - uses: dagger/dagger-for-github@" + "0" * 40 + "\n"
        "        with:\n"
        "          args: release-candidate --tag=${{ inputs.tag }}\n",
        encoding="utf-8",
    )
    # Then the audit reports both sinks
    assert _injectable_sinks(tmp_path) == [
        "bad.yml: echo ${{ github.event.pull_request.title }}",
        "bad.yml: release-candidate --tag=${{ inputs.tag }}",
    ]


def test_should_keep_oidc_publisher_source_free_and_shell_free() -> None:
    document = _workflow("publish.yml")
    publish = _job(document, "publish")
    steps = _steps(publish)
    triggers = _mapping(document.get("on"))
    workflow_run = _mapping(triggers.get("workflow_run"))
    assert workflow_run.get("workflows") == ["Dagger release candidate"]
    assert workflow_run.get("types") == ["completed"]
    condition = str(publish.get("if", ""))
    assert "workflow_run.conclusion == 'success'" in condition
    assert "workflow_run.event == 'workflow_dispatch'" in condition
    assert "workflow_run.head_branch == github.event.repository.default_branch" in condition
    assert _mapping(publish.get("permissions")) == {
        "actions": "read",
        "contents": "read",
        "id-token": "write",
    }
    # Lineage is proven by the central Dagger function; no step runs repository shell,
    # and nothing checks out or builds source.
    assert [_action(step) for step in steps] == [DAGGER_ACTION, DOWNLOAD_ACTION, PUBLISH_ACTION]
    assert all("run" not in step for step in steps)
    download = _mapping(steps[1].get("with"))
    assert download.get("name") == "edge-proc-${{ github.event.workflow_run.head_sha }}"
    assert download.get("run-id") == "${{ github.event.workflow_run.id }}"
    assert download.get("github-token") == "${{ github.token }}"
    settings = _mapping(steps[2].get("with"))
    assert settings.get("packages-dir") == "release/dist"
    assert settings.get("attestations") is True


#: The central lineage proof (hseshadr/ci#49), pinned at a literal hseshadr/ci commit.
LINEAGE_MODULE = re.compile(r"^github\.com/hseshadr/ci/modules/portfolio-foundation@[0-9a-f]{40}$")
#: Exact args: every value is a quoted env var bound to the triggering run, so a
#: hard-coded run id or SHA cannot make the proof about a different run.
LINEAGE_ARGS = (
    'release-lineage --github-token=env:GH_TOKEN --repository="$GITHUB_REPOSITORY" '
    '--run-id="$RUN_ID" --head-sha="$HEAD_SHA" --publish-run-id="$GITHUB_RUN_ID"'
)


def test_should_prove_the_candidate_lineage_in_dagger_before_touching_any_artifact() -> None:
    # Given the first publish step. The job `if` (head_branch == default_branch) is
    # satisfied by a dispatch on a TAG named `main`, so this proof must run first.
    lineage = _steps(_job(_workflow("publish.yml"), "publish"))[0]
    invocation = _mapping(lineage.get("with"))

    # Then it is the central release-lineage call, fed only through quoted env values
    assert _action(lineage) == DAGGER_ACTION
    assert _mapping(lineage.get("env")) == {
        "GH_TOKEN": "${{ github.token }}",
        "RUN_ID": "${{ github.event.workflow_run.id }}",
        "HEAD_SHA": "${{ github.event.workflow_run.head_sha }}",
    }
    assert LINEAGE_MODULE.fullmatch(str(invocation.pop("module", "")))
    assert invocation == {"version": "0.21.8", "verb": "call", "args": LINEAGE_ARGS}


def test_should_paste_no_expression_into_any_publisher_dagger_input() -> None:
    # Given every Dagger step of the publisher (the action pastes these into bash)
    steps = _steps(_job(_workflow("publish.yml"), "publish"))
    pasted = [
        str(value)
        for step in steps
        if _action(step) == DAGGER_ACTION
        for key, value in _mapping(step.get("with")).items()
        if key != "module"
    ]

    # Then there is one, and no `${{ }}` expression reaches script text
    assert pasted
    assert [value for value in pasted if "${{" in value] == []


@pytest.mark.parametrize("workflow", ["ci.yml", "dagger-shadow.yml"])
def test_should_delete_superseded_ci_ingress(workflow: str) -> None:
    assert not (WORKFLOWS / workflow).exists()
