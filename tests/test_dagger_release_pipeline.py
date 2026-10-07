from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_contract.py"
SHA = "a" * 40


def _load_contract() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_contract_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_source(root: Path, version: str = "1.2.3", stable: str = "1.2.3") -> None:
    package = root / "edgeproc"
    package.mkdir(parents=True)
    (package / "_version.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (root / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [Unreleased]\n\n## [{stable}] - 2026-08-25\n",
        encoding="utf-8",
    )


def _write_wheel(dist: Path, name: str = "edge_proc", version: str = "1.2.3") -> None:
    dist.mkdir(parents=True, exist_ok=True)
    path = dist / f"{name}-{version}-py3-none-any.whl"
    metadata = f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{name}-{version}.dist-info/METADATA", metadata)


def _write_sdist(dist: Path, name: str = "edge_proc", version: str = "1.2.3") -> None:
    dist.mkdir(parents=True, exist_ok=True)
    root = f"{name}-{version}"
    payload = f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n".encode()
    info = tarfile.TarInfo(f"{root}/PKG-INFO")
    info.size = len(payload)
    with tarfile.open(dist / f"{root}.tar.gz", "w:gz") as archive:
        archive.addfile(info, io.BytesIO(payload))


def _write_distributions(dist: Path, name: str = "edge_proc", version: str = "1.2.3") -> None:
    _write_wheel(dist, name, version)
    _write_sdist(dist, name, version)


def _hosted_payload(sha: str = SHA, conclusion: str = "success", tag_sha: str | None = None) -> str:
    return json.dumps(
        {
            "main_sha": sha,
            "tag_sha": tag_sha or sha,
            "check_runs": [
                {"name": "Dagger", "head_sha": sha, "conclusion": conclusion},
            ],
        }
    )


def test_should_accept_release_identity_when_tag_version_and_changelog_match(
    tmp_path: Path,
) -> None:
    # Given
    contract = _load_contract()
    _write_source(tmp_path)

    # When / Then
    contract.validate_source_identity(tmp_path, "v1.2.3")


@pytest.mark.parametrize(
    ("version", "stable", "tag"),
    [("1.2.3", "1.2.3", "v1.2.4"), ("1.2.3", "1.2.2", "v1.2.3")],
)
def test_should_reject_release_identity_when_source_is_not_exact(
    tmp_path: Path, version: str, stable: str, tag: str
) -> None:
    # Given
    contract = _load_contract()
    _write_source(tmp_path, version, stable)

    # When / Then
    with pytest.raises(ValueError, match="release identity"):
        contract.validate_source_identity(tmp_path, tag)


def test_should_accept_archives_when_project_and_version_match_source(tmp_path: Path) -> None:
    # Given
    contract = _load_contract()
    _write_source(tmp_path)
    dist = tmp_path / "dist"
    _write_distributions(dist)

    # When / Then
    contract.validate_distributions(tmp_path, dist, "v1.2.3")


@pytest.mark.parametrize(("name", "version"), [("other", "1.2.3"), ("edge_proc", "9.9.9")])
def test_should_reject_archives_when_metadata_is_not_exact(
    tmp_path: Path, name: str, version: str
) -> None:
    # Given
    contract = _load_contract()
    _write_source(tmp_path)
    dist = tmp_path / "dist"
    _write_distributions(dist, name, version)

    # When / Then
    with pytest.raises(ValueError, match="distribution metadata"):
        contract.validate_distributions(tmp_path, dist, "v1.2.3")


def test_should_reject_hosted_eligibility_when_main_or_dagger_check_is_not_exact() -> None:
    # Given
    contract = _load_contract()

    # When / Then
    with pytest.raises(ValueError, match="hosted eligibility"):
        contract.validate_hosted_eligibility(_hosted_payload("b" * 40), SHA)
    with pytest.raises(ValueError, match="hosted eligibility"):
        contract.validate_hosted_eligibility(_hosted_payload(SHA, "failure"), SHA)
    with pytest.raises(ValueError, match="hosted eligibility"):
        contract.validate_hosted_eligibility(_hosted_payload(SHA, tag_sha="b" * 40), SHA)


def test_should_fetch_only_hosted_identity_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    contract = _load_contract()
    responses = iter(
        [
            io.BytesIO(json.dumps({"sha": SHA}).encode()),
            io.BytesIO(json.dumps({"sha": SHA}).encode()),
            io.BytesIO(
                json.dumps(
                    {
                        "check_runs": [
                            {
                                "name": "Dagger",
                                "head_sha": SHA,
                                "conclusion": "success",
                                "untrusted_extra": "discarded",
                            }
                        ]
                    }
                ).encode()
            ),
        ]
    )
    monkeypatch.setattr(contract, "urlopen", lambda *_args, **_kwargs: next(responses))

    # When
    payload = contract.fetch_hosted_payload("gainratio/edge-proc", SHA, "v1.2.3", "token")

    # Then
    contract.validate_hosted_eligibility(payload, SHA)
    assert "untrusted_extra" not in payload


def test_should_record_literal_sha256_for_each_candidate_archive(tmp_path: Path) -> None:
    # Given
    contract = _load_contract()
    dist = tmp_path / "dist"
    _write_distributions(dist)
    output = tmp_path / "SHA256SUMS"

    # When
    contract.write_checksums(dist, output)

    # Then
    expected = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  dist/{path.name}"
        for path in sorted(dist.iterdir())
    ]
    assert output.read_text(encoding="utf-8").splitlines() == expected
