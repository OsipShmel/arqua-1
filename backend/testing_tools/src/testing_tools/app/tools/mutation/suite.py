"""The agent's pytest suite: fetch it, give it a Python, run it, read the outcomes."""

import json
import os
import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from testing_tools.api.schemas import (
    ErrorCode,
    GitTestSuite,
    InlineTestSuite,
    MutationConfig,
    TestCaseResult,
    TestOutcome,
)
from testing_tools.app.tools.base import ToolError
from testing_tools.app.tools.process import ProcessResult, run_process

PLUGIN = Path(__file__).with_name("arqa_report_plugin.py")
BASE_REQUIREMENTS = ("pytest", "httpx")
GIT_TIMEOUT_S = 300.0
INSTALL_TIMEOUT_S = 600.0
# pytest exit codes: 0 ok, 1 tests failed, 5 nothing collected; 2-4 = the run itself broke
# (collection errors don't stop the run: --continue-on-collection-errors)
RUN_BROKEN_EXIT_CODES = {2, 3, 4}


@dataclass(frozen=True)
class SuiteRun:
    process: ProcessResult
    tests: list[TestCaseResult]
    collection_errors: list[str]
    exitstatus: int | None
    """None when pytest left no report (crash, kill on timeout)."""

    @property
    def broken(self) -> bool:
        return self.exitstatus is None or self.exitstatus in RUN_BROKEN_EXIT_CODES

    def outcomes(self) -> dict[str, TestOutcome]:
        return {t.node_id: t.outcome for t in self.tests}


class Suite:
    def __init__(self, root: Path, python: str, work_dir: Path) -> None:
        self.root = root
        self._python = python
        self._work_dir = work_dir
        self._plugin_dir = work_dir / "plugin"
        self._plugin_dir.mkdir(exist_ok=True)
        shutil.copy(PLUGIN, self._plugin_dir / PLUGIN.name)

    @classmethod
    async def prepare(cls, config: MutationConfig, work_dir: Path, uv_bin: str) -> "Suite":
        root = await _materialize(config, work_dir / "suite")
        python = await _python(config.requirements, work_dir, uv_bin)
        return cls(root, python, work_dir)

    async def run(
        self,
        config: MutationConfig,
        base_url: str,
        timeout_s: float,
        *,
        deselect: Sequence[str] = (),
        junit: Path | None = None,
    ) -> SuiteRun:
        report = self._work_dir / "pytest-report.json"
        report.unlink(missing_ok=True)
        args = [
            self._python, "-m", "pytest",
            "-p", PLUGIN.stem,
            "-p", "no:cacheprovider",
            "--continue-on-collection-errors",
            "-q", "--rootdir", str(self.root),
        ]  # fmt: skip
        if junit is not None:
            args += ["--junitxml", str(junit)]
        for node_id in deselect:
            args += ["--deselect", node_id]
        args += config.pytest_args

        env = {k: v for k, v in os.environ.items() if not k.startswith("TT_")}
        env.update(
            {
                config.base_url_env: base_url,
                "ARQA_REPORT_PATH": str(report),
                "PYTHONPATH": os.pathsep.join([str(self._plugin_dir), str(self.root)]),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        process = await run_process(args, cwd=self.root, env=env, timeout_s=timeout_s)
        if process.timed_out or not report.exists():
            return SuiteRun(process, [], [], None)
        data = json.loads(report.read_text())
        tests = [
            TestCaseResult(
                node_id=node_id,
                outcome=TestOutcome(record["outcome"]),
                duration_s=round(record["duration"], 4),
                message=record["message"],
            )
            for node_id, record in data["tests"].items()
        ]
        return SuiteRun(process, tests, list(data["collection_errors"]), int(data["exitstatus"]))


async def _materialize(config: MutationConfig, dest: Path) -> Path:
    match config.tests:
        case InlineTestSuite(files=files):
            for rel, content in files.items():
                path = dest / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            return dest
        case GitTestSuite() as git:
            await _clone(git, dest)
            root = (dest / git.subdir).resolve()
            if not root.is_relative_to(dest.resolve()) or not root.is_dir():
                raise ToolError(
                    ErrorCode.TEST_SUITE_INVALID, f"subdir {git.subdir!r} is not in the repository"
                )
            return root


async def _clone(git: GitTestSuite, dest: Path) -> None:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    shallow = ["--depth", "1"] if git.ref == "HEAD" else []
    steps = [["git", "clone", "--quiet", *shallow, "--", git.repo_url, str(dest)]]
    if git.ref != "HEAD":
        steps.append(["git", "-C", str(dest), "checkout", "--quiet", git.ref])
    for args in steps:
        result = await run_process(args, cwd=dest.parent, env=env, timeout_s=GIT_TIMEOUT_S)
        if result.returncode != 0:
            raise ToolError(
                ErrorCode.TEST_SUITE_INVALID,
                f"git {args[1]} failed for {git.repo_url}@{git.ref}",
                {"output_tail": result.tail()},
            )


async def _python(requirements: list[str], work_dir: Path, uv_bin: str) -> str:
    """Our own interpreter (it has pytest and httpx) unless the suite needs more packages."""
    if not requirements:
        return sys.executable
    bad = [r for r in requirements if r.strip().startswith("-")]
    if bad:
        raise ToolError(ErrorCode.TEST_SUITE_INVALID, f"requirements must not be options: {bad}")
    venv = work_dir / "venv"
    python = venv / "bin" / "python"
    for args in (
        [uv_bin, "venv", "--quiet", "--python", sys.executable, str(venv)],
        [uv_bin, "pip", "install", "--quiet", "--python", str(python),
         *BASE_REQUIREMENTS, *requirements],
    ):  # fmt: skip
        try:
            result = await run_process(args, cwd=work_dir, timeout_s=INSTALL_TIMEOUT_S)
        except FileNotFoundError as exc:
            raise ToolError(ErrorCode.TOOL_FAILED, f"{uv_bin} is not installed") from exc
        if result.returncode != 0:
            raise ToolError(
                ErrorCode.TEST_SUITE_INVALID,
                "could not install test requirements",
                {"output_tail": result.tail()},
            )
    return str(python)
