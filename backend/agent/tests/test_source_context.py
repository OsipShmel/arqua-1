import pytest

from agent.source_context import read_source_context


def test_source_is_static_bounded_and_excludes_environments(tmp_path):
    (tmp_path / "app.py").write_text(
        "from fastapi import FastAPI\nraise RuntimeError('must not execute')\n"
        "app = FastAPI()\n@app.get('/items')\nasync def items():\n    return {'id': 1}\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("API_KEY=do-not-include")
    hidden = tmp_path / ".venv"
    hidden.mkdir()
    (hidden / "private.py").write_text("password = 'do-not-include'")
    (tmp_path / "broken.py").write_text("def broken(:")
    context = read_source_context(tmp_path)
    assert len(context["files"]) == 1
    unit = context["files"][0]["units"][0]
    assert unit["name"] == "items" and unit["decorators"] == ["app.get('/items')"]
    assert "must not execute" not in str(context)
    assert "do-not-include" not in str(context)
    assert context["skipped"][0]["reason"] == "SyntaxError"


def test_source_budget_marks_omitted_files(tmp_path):
    (tmp_path / "a.py").write_text("def first():\n    return 1\n")
    (tmp_path / "b.py").write_text("def second():\n    return 2\n")
    context = read_source_context(tmp_path, max_chars=350)
    assert len(context["files"]) == 1 and context["truncated"] is True
    assert context["skipped"][0]["reason"] == "context_budget"


def test_invalid_source_fails_clearly(tmp_path):
    with pytest.raises(ValueError, match="Каталог исходников"):
        read_source_context(tmp_path / "missing")
    with pytest.raises(ValueError, match="Python"):
        read_source_context(tmp_path)
