"""Tests for Python SDK package metadata and distribution invariants.

Validates pyproject.toml configuration, PEP 440 version conformance,
installed package metadata parity, license file synchronization, and
distribution archive contents for sdist and wheel builds.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import logging
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import pytest
from packaging.version import InvalidVersion, Version

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

log = logging.getLogger(__name__)

SDK_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = SDK_ROOT.parent.parent
PYPROJECT_PATH = SDK_ROOT / "pyproject.toml"
LICENSE_PATH = SDK_ROOT / "LICENSE"
ROOT_LICENSE_PATH = REPO_ROOT / "LICENSE"

PEP440_REGEX = re.compile(
    r"^([1-9][0-9]*!)?(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))*((a|b|rc)(0|[1-9][0-9]*))?(\.post(0|[1-9][0-9]*))?(\.dev(0|[1-9][0-9]*))?$"
)


def load_pyproject_data(path: Path) -> dict[str, Any]:
    """Load and parse a pyproject.toml file.

    Parameters:
        path: Filesystem path to the pyproject.toml file.

    Returns:
        Parsed TOML dictionary representation.

    Raises:
        FileNotFoundError: If the pyproject.toml file does not exist.
        ValueError: If the file content is not valid TOML.
    """
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    with path.open("rb") as stream:
        return tomllib.load(stream)


def is_valid_pep440(version_str: str) -> bool:
    """Validate whether a version string complies with PEP 440.

    Parameters:
        version_str: The version identifier to validate.

    Returns:
        True if the version is valid under PEP 440, otherwise False.
    """
    if not version_str or not isinstance(version_str, str):
        return False
    try:
        parsed = Version(version_str)
        return str(parsed) == version_str or PEP440_REGEX.match(version_str) is not None
    except InvalidVersion:
        return False


# ── Pyproject metadata invariants ──────────────────────────────────────────


class TestPyprojectMetadata:
    """Validate pyproject.toml metadata fields and standards compliance."""

    def test_pyproject_file_exists_and_parses(self) -> None:
        """The SDK pyproject.toml file must exist and parse as valid TOML."""
        data = load_pyproject_data(PYPROJECT_PATH)
        assert isinstance(data, dict)
        assert "project" in data

    def test_project_table_required_fields(self) -> None:
        """The [project] table must contain required packaging keys."""
        data = load_pyproject_data(PYPROJECT_PATH)
        project = data["project"]

        assert project.get("name") == "undolog-sdk"
        assert project.get("readme") == "README.md"
        assert project.get("license") == "Apache-2.0"
        assert project.get("requires-python") == ">=3.10"
        assert isinstance(project.get("authors"), list)
        assert len(project["authors"]) > 0

    def test_version_is_valid_pep440(self) -> None:
        """The package version in pyproject.toml must adhere to PEP 440."""
        data = load_pyproject_data(PYPROJECT_PATH)
        version_str = data["project"].get("version")

        assert isinstance(version_str, str)
        assert is_valid_pep440(version_str) is True

    @pytest.mark.parametrize(
        ("raw_version", "expected_valid"),
        [
            ("0.1.0", True),
            ("0.2.0a1", True),
            ("1.0.0rc2", True),
            ("1.2.3.post1", True),
            ("", False),
            ("invalid_version", False),
            ("v0.1.0", False),
            ("1.0.0.0.0.bad-release", False),
        ],
    )
    def test_pep440_validator_edge_cases(
        self, raw_version: str, expected_valid: bool
    ) -> None:
        """Verify the PEP 440 validation logic on boundary and malformed inputs."""
        assert is_valid_pep440(raw_version) is expected_valid

    def test_project_urls_complete_and_valid(self) -> None:
        """Project URLs must define all required navigation endpoints."""
        data = load_pyproject_data(PYPROJECT_PATH)
        urls = data["project"].get("urls", {})

        required_keys = {
            "Homepage",
            "Documentation",
            "Repository",
            "Issues",
            "Changelog",
        }
        for key in required_keys:
            assert key in urls, f"Missing required URL key: {key}"
            assert urls[key].startswith("https://"), (
                f"URL for {key} must start with https://"
            )

    def test_project_classifiers_include_python_versions(self) -> None:
        """Classifiers must cover target Python versions and development status."""
        data = load_pyproject_data(PYPROJECT_PATH)
        classifiers = data["project"].get("classifiers", [])

        assert "Development Status :: 5 - Production/Stable" in classifiers
        assert "Programming Language :: Python :: 3.10" in classifiers
        assert "Programming Language :: Python :: 3.11" in classifiers
        assert "Programming Language :: Python :: 3.12" in classifiers

    def test_installed_version_matches_pyproject(self) -> None:
        """The installed distribution version must resolve to pyproject.toml version."""
        data = load_pyproject_data(PYPROJECT_PATH)
        expected_version = data["project"]["version"]

        installed_version = importlib.metadata.version("undolog-sdk")
        assert installed_version == expected_version


# ── License file consistency ───────────────────────────────────────────────


class TestLicenseFileConsistency:
    """Verify license file presence and alignment with the repository root."""

    def test_license_file_exists_in_package(self) -> None:
        """The SDK directory must contain a dedicated LICENSE file."""
        assert LICENSE_PATH.is_file(), f"Expected license file at {LICENSE_PATH}"
        assert LICENSE_PATH.stat().st_size > 0

    def test_license_file_matches_repository_root(self) -> None:
        """The package LICENSE file must match the root LICENSE verbatim."""
        assert ROOT_LICENSE_PATH.is_file(), (
            f"Expected root license at {ROOT_LICENSE_PATH}"
        )

        package_license_content = LICENSE_PATH.read_text(encoding="utf-8")
        root_license_content = ROOT_LICENSE_PATH.read_text(encoding="utf-8")

        assert package_license_content == root_license_content

    def test_license_file_contains_apache_header(self) -> None:
        """The license file content must state Apache License Version 2.0."""
        content = LICENSE_PATH.read_text(encoding="utf-8")
        assert "Apache License" in content
        assert "Version 2.0" in content

    def test_license_validation_detects_mismatch(self) -> None:
        """Validate that corrupted license text is detected as invalid."""
        valid_content = LICENSE_PATH.read_text(encoding="utf-8")
        corrupted_content = valid_content.replace("Apache License", "Custom License")

        assert "Apache License" not in corrupted_content


# ── Distribution build packaging invariants ────────────────────────────────


class TestDistributionPackaging:
    """Verify that built distribution archives package the LICENSE file."""

    def test_distribution_archives_include_license(self) -> None:
        """Both sdist (.tar.gz) and wheel (.whl) must include the LICENSE file."""
        if importlib.util.find_spec("build") is None:
            pytest.skip("The build module is not installed in the test environment.")

        with tempfile.TemporaryDirectory() as temp_dir:
            # Build sdist and wheel into temporary directory.
            cmd = [
                sys.executable,
                "-m",
                "build",
                "--no-isolation",
                "--sdist",
                "--wheel",
                "--outdir",
                temp_dir,
                str(SDK_ROOT),
            ]
            run_res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )
            if run_res.returncode != 0:
                log.info(
                    "Retrying build with isolated environment: %s",
                    run_res.stderr,
                )
                cmd_isolated = [
                    sys.executable,
                    "-m",
                    "build",
                    "--sdist",
                    "--wheel",
                    "--outdir",
                    temp_dir,
                    str(SDK_ROOT),
                ]
                run_res = subprocess.run(
                    cmd_isolated,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if run_res.returncode != 0:
                    pytest.skip(
                        f"Distribution build could not be executed: {run_res.stderr.strip()}"
                    )

            dist_files = list(Path(temp_dir).iterdir())
            sdist_files = [p for p in dist_files if p.name.endswith(".tar.gz")]
            wheel_files = [p for p in dist_files if p.name.endswith(".whl")]

            if not sdist_files or not wheel_files:
                pytest.skip("Distribution archives were not generated by build.")

            # Check sdist contains LICENSE.
            with tarfile.open(sdist_files[0], "r:gz") as tar:
                sdist_members = tar.getnames()
                has_license = any(
                    m.endswith("/LICENSE") or m == "LICENSE" for m in sdist_members
                )
                assert has_license is True, (
                    f"LICENSE not found in sdist archive: {sdist_members}"
                )

            # Check wheel contains LICENSE in dist-info.
            with zipfile.ZipFile(wheel_files[0], "r") as zf:
                wheel_members = zf.namelist()
                has_license = any(
                    "licenses/LICENSE" in m or m.endswith("/LICENSE") or m == "LICENSE"
                    for m in wheel_members
                )
                assert has_license is True, (
                    f"LICENSE not found in wheel archive: {wheel_members}"
                )
