"""README contract: the first screen stays plain, true, and wired to real files.

Deliberately dumb: string and regex checks only, no markdown parser. Its job is to stop the
README drifting (tagline vs package metadata, badge creep, section order, jargon creeping
back in, a dead link), not to judge prose.
"""

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
TAGLINE_LIMIT = 160
BADGE_LIMIT = 3
#: The plain-English README shape, in order. `## Try it` must come before the explanation.
SECTION_ORDER = (
    "## Try it",
    "## How it works",
    "## How it fits with the related projects",
    "## What it does not do",
    "## When to use something else",
    "## Install",
    "## Develop",
    "## More detail",
    "## License",
)
TECHNICAL_DOCS_LINE = "**Technical docs:**"
GETTING_STARTED = "docs/GETTING_STARTED.md"
ARCHITECTURE = "docs/ARCHITECTURE.md"
ARCHITECTURE_MAP = "docs/architecture/index.html"
ARCHITECTURE_SOURCE = "docs/architecture/runtime.architecture.json"
#: Internal vocabulary and hype the README must not use (checked outside code spans).
BANNED_WORDS = (
    "northstar",
    "seam",
    "lego",
    "trust envelope",
    "receipt",
    "fail-closed",
    "gate",
    "fleet",
    "portfolio",
    "substrate",
    "production-ready",
    "robust",
    "blazing",
    "enterprise-grade",
    "seamless",
)
#: The retired template headings this rewrite replaced.
RETIRED_HEADINGS = ("## At a glance", "## Try it in 60 seconds", "BELOW THE FOLD")
# [text](target): the target stops at whitespace or ")"; an optional "title" is ignored.
LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _tagline() -> str:
    lines = [line.strip() for line in README.splitlines()[1:]]
    return next(line for line in lines if line and not line.startswith("[!["))


def _prose() -> str:
    """README text with fenced code blocks and inline code removed."""
    without_fences = re.sub(r"```.*?```", "", README, flags=re.DOTALL)
    return re.sub(r"`[^`\n]*`", "", without_fences)


def _section(heading: str) -> str:
    body = README.split(f"\n{heading}\n", maxsplit=1)[1]
    return body.split("\n## ", maxsplit=1)[0]


def test_first_line_is_the_project_name() -> None:
    assert README.splitlines()[0] == "# EdgeProc"


def test_tagline_is_one_short_sentence_equal_to_the_package_description() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    tagline = _tagline()

    assert len(tagline) <= TAGLINE_LIMIT
    assert tagline == project["description"]
    assert ". " not in tagline
    assert tagline.endswith(".")


def test_fastest_way_to_try_it_is_bold_and_right_under_the_tagline() -> None:
    after_tagline = README.split(_tagline(), maxsplit=1)[1].lstrip("\n")
    first_block = after_tagline.split("\n\n", maxsplit=1)[0]
    assert first_block.startswith("**")
    assert 'pip install "edge-proc[bundles]"' in first_block


def test_readme_carries_at_most_three_badges() -> None:
    assert README.count("[![") <= BADGE_LIMIT


def test_sections_appear_in_the_plain_english_order() -> None:
    positions = [README.index(f"\n{heading}\n") for heading in SECTION_ORDER]
    assert positions == sorted(positions)


@pytest.mark.parametrize("heading", RETIRED_HEADINGS)
def test_retired_template_headings_are_gone(heading: str) -> None:
    assert heading not in README


def test_technical_docs_line_sits_above_try_it_and_links_architecture_and_getting_started() -> None:
    intro = README.split("\n## Try it\n", maxsplit=1)[0]
    line = next(line for line in intro.splitlines() if line.startswith(TECHNICAL_DOCS_LINE))
    targets = [target for _text, target in LINK.findall(line)]
    assert targets[0] == ARCHITECTURE
    assert GETTING_STARTED in targets


def test_develop_section_links_getting_started() -> None:
    assert GETTING_STARTED in _section("## Develop")


@pytest.mark.parametrize("word", BANNED_WORDS)
def test_readme_prose_avoids_internal_jargon_and_hype(word: str) -> None:
    pattern = rf"\b{re.escape(word)}"
    assert not re.search(pattern, _prose(), re.IGNORECASE), f"README prose uses {word!r}"


def test_jargon_check_would_catch_a_banned_word() -> None:
    """Guards the check above against a prose filter that silently strips everything."""
    assert "signed" in _prose()
    assert re.search(r"\bgate", "the release gate", re.IGNORECASE)


def test_readme_explains_how_the_related_projects_fit() -> None:
    fit = _section("## How it fits with the related projects")
    for name in ("edgeproc-core", "@edgeproc/browser", "privacy-core"):
        assert name in fit, name


def test_readme_links_the_interactive_architecture_map() -> None:
    linked = [
        target
        for text, target in LINK.findall(README)
        if "Explore the interactive architecture map" in text
    ]
    assert linked == [ARCHITECTURE_MAP]
    assert (ROOT / ARCHITECTURE_SOURCE).is_file()


@pytest.mark.parametrize(
    "doc",
    [
        ARCHITECTURE,
        GETTING_STARTED,
        "docs/QUICKSTART.md",
        "docs/CONFIGURATION.md",
        "docs/OPERATIONS.md",
        "ROADMAP.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
    ],
)
def test_more_detail_links_every_technical_doc(doc: str) -> None:
    assert doc in [target for _text, target in LINK.findall(_section("## More detail"))]


def _relative_targets() -> list[str]:
    targets = (target for _text, target in LINK.findall(README))
    return [
        target.split("#", maxsplit=1)[0]
        for target in targets
        if not re.match(r"^[a-z][a-z0-9+.-]*:", target) and not target.startswith("#")
    ]


def test_readme_has_relative_links_to_check() -> None:
    """Guards the link check below against a regex that silently matches nothing."""
    assert ARCHITECTURE_MAP in _relative_targets()


@pytest.mark.parametrize("target", sorted(set(_relative_targets())))
def test_every_relative_link_resolves(target: str) -> None:
    assert (ROOT / target).exists(), f"README links {target!r}, which is not in the repo"


@pytest.mark.parametrize(
    "heading",
    [
        "## Prerequisites",
        "## Clone, install, and run it",
        "## Run the full check",
        "## Map of the code",
        "## Make your first change",
        "## Open a pull request",
    ],
)
def test_getting_started_covers_every_step_a_new_developer_needs(heading: str) -> None:
    assert f"\n{heading}\n" in (ROOT / GETTING_STARTED).read_text(encoding="utf-8")
