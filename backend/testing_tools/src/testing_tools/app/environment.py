"""Versions that make a run reproducible: interpreter, gateway and tool packages."""

import platform
from functools import cache
from importlib.metadata import PackageNotFoundError, version

PACKAGES = ("testing-tools", "schemathesis", "pytest", "httpx", "fastapi", "uvicorn")


@cache
def environment() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for package in PACKAGES:
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            continue
    return versions
