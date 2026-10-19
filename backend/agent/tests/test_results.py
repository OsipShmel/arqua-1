import base64
from pathlib import Path

import pytest

from agent.results import AgentResult, ResultFile, save_result_files


def test_files_are_saved_without_execution_and_junit_is_preserved(tmp_path):
    result = AgentResult.model_validate(
        {
            "status": "success",
            "output": "done",
            "tool_mode": "real",
            "files": [
                {
                    "path": "tests/test_api.py",
                    "kind": "pytest",
                    "content": "raise RuntimeError('must not execute')",
                },
                {
                    "path": "junit.xml",
                    "kind": "junit",
                    "encoding": "base64",
                    "content": base64.b64encode(b"<testsuites/>").decode(),
                },
            ],
        }
    )
    files = save_result_files(tmp_path, result.files)
    assert len(files) == 2
    assert Path(files[0]["path"]).read_text(encoding="utf-8").startswith("raise")
    assert Path(files[1]["path"]).read_bytes() == b"<testsuites/>"


@pytest.mark.parametrize(
    "path",
    [
        "../outside.py",
        "/absolute.py",
        "C:/outside.py",
        "a\\outside.py",
        "NUL.py",
        "a/../test.py",
        "a?b.py",
        "a./b.py",
    ],
)
def test_artifact_paths_are_confined(tmp_path, path):
    with pytest.raises(ValueError):
        save_result_files(tmp_path, [ResultFile(path=path, content="x")])
    assert not (tmp_path / "agent_artifacts").exists()


@pytest.mark.parametrize("names", [["same", "SAME"], ["a", "a/b"], ["a/b", "a"]])
def test_entire_file_batch_is_validated_before_writing(tmp_path, names):
    with pytest.raises(ValueError):
        save_result_files(
            tmp_path, [ResultFile(path=name, content="x") for name in names]
        )
    assert not (tmp_path / "agent_artifacts").exists()


def test_legacy_result_is_accepted_without_claiming_real_checks():
    assert (
        AgentResult.model_validate({"status": "success", "output": "legacy"}).tool_mode
        == "unknown"
    )
    with pytest.raises(ValueError):
        AgentResult.model_validate({"status": "success"})
