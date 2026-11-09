"""А-21 (С-1, П-1): развёртывание на чистом стенде.

Шаги: на чистых стендах Windows, Linux и macOS: установка по README (lock-файл),
развёртывание демо-сервисов в Docker, выполнение А-1.
Ожидается: система развёрнута и работает без правки кода; зависимости установлены из
lock-файла.

Чистые стенды здесь не поднять. Проверяется то, от чего зависит установка: согласованность
lock-файла, единая версия Python, команды в README, Docker-файлы и матрица CI.
"""

import re
import shutil
import subprocess
import tomllib

import pytest
import yaml
from conftest import ROOT


def test_lock_file_matches_the_project() -> None:
    check = subprocess.run(
        ["uv", "lock", "--check"], cwd=ROOT, capture_output=True, text=True
    )

    assert check.returncode == 0, check.stderr


def test_one_python_version_for_the_workspace() -> None:
    root = (ROOT / ".python-version").read_text().strip()
    for pin in ROOT.glob("backend/*/.python-version"):
        assert pin.read_text().strip() == root, (
            f"{pin.relative_to(ROOT)} makes uv pick another interpreter"
        )
    for manifest in [ROOT / "pyproject.toml", *ROOT.glob("backend/*/pyproject.toml")]:
        requires = tomllib.loads(manifest.read_text())["project"]["requires-python"]
        assert requires == ">=3.13", manifest


def test_root_install_brings_every_package() -> None:
    root = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert set(root["project"]["dependencies"]) == {
        "agent",
        "openapi-parser",
        "testing-tools",
    }


def test_readme_installs_from_the_lock_file() -> None:
    readme = (ROOT / "README.md").read_text("utf-8")

    assert "uv sync --locked" in readme
    assert "docker compose up" in readme


def test_dockerfile_copies_existing_paths() -> None:
    dockerfile = (ROOT / "backend/testing_tools/Dockerfile").read_text()
    for line in dockerfile.splitlines():
        if line.startswith("COPY") and "--from" not in line:
            *sources, _ = line.split()[1:]
            for source in sources:
                assert (ROOT / source).exists(), source


def test_compose_file_is_valid() -> None:
    if not shutil.which("docker"):
        pytest.skip("docker CLI not installed")
    check = subprocess.run(
        ["docker", "compose", "config", "-q"], cwd=ROOT, capture_output=True, text=True
    )

    assert check.returncode == 0, check.stderr


def test_ci_covers_three_operating_systems() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/cli.yml").read_text())
    matrix = workflow["jobs"]["cli"]["strategy"]["matrix"]

    assert set(matrix["os"]) == {"ubuntu-latest", "windows-latest", "macos-latest"}
    assert any(
        re.search(r"uv sync --locked", str(step.get("run", "")))
        for step in workflow["jobs"]["cli"]["steps"]
    )
