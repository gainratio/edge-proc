from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _locked_packages() -> list[dict[str, object]]:
    document = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = document.get("package")
    assert isinstance(packages, list)
    return [package for package in packages if isinstance(package, dict)]


def test_should_resolve_cpu_only_torch_when_ci_has_no_accelerator() -> None:
    # Given
    packages = _locked_packages()
    names = {str(package.get("name", "")) for package in packages}
    torch = next(package for package in packages if package.get("name") == "torch")

    # When
    source = torch.get("source")

    # Then
    assert not any(name.startswith("nvidia-") for name in names)
    assert isinstance(source, dict)
    assert source.get("registry") == "https://download.pytorch.org/whl/cpu"


def test_should_lock_audit_and_build_tools_used_by_dagger() -> None:
    # Given
    packages = _locked_packages()
    versions = {str(package.get("name")): str(package.get("version")) for package in packages}
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    # When
    build_requirements = project["build-system"]["requires"]

    # Then: Dagger builds with no isolation from the locked hatchling, so the build
    # requirement is exactly the locked version (read from uv.lock, never a copy), and
    # the audit runs the locked pip-audit (the Dagger audit constrains to uv.lock).
    assert build_requirements == [f"hatchling=={versions['hatchling']}"]
    assert "pip-audit" in versions
